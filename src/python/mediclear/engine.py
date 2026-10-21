# -*- coding: utf-8 -*-
"""计算引擎门面：把各险种模块编排成一次完整的报销测算。

对外主入口是 :class:`ReimbursementCalculator`，它保持了旧版 API 的
构造签名与返回结构，新增能力（慢特病、公式说明、政策版本）都是**附加键**，
不会破坏已有调用方::

    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient')
    result = calc.calculate([{'name': '西药费', 'amount': 3000, 'category': 'class_b'}])
    result['summary']['total_reimburse']

编排顺序::

    费用明细
      ↓ 基本医保 / 门诊慢特病
    统筹报销额
      ↓ 总费用 - 统筹报销额
    个人自付
      ↓ 大病保险
    最终报销额与自付额
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .constants import DEFAULT_HOSPITAL_LEVEL
from .data.repository import PolicyRepository, get_repository
from .exceptions import PolicyNotFoundError
from .policies.base import resolve_tiers, round_money, round_ratio
from .policies.basic import BasicMedicalInsurance
from .policies.chronic import ChronicDiseasePolicy
from .policies.serious_illness import SeriousIllnessInsurance

__all__ = ['ReimbursementCalculator', 'quick_calculate', 'ENGINE_VERSION']

ENGINE_VERSION = '2.0.0'


class ReimbursementCalculator:
    """医保报销计算器。

    参数与旧版完全一致，可直接替换旧实现::

        ReimbursementCalculator(city_code, insurance_type, visit_type='inpatient',
                                hospital_level=None, year_accumulated=0)

    Args:
        city_code: 城市代码，如 ``'beijing'``
        insurance_type: ``'employee'``（职工）或 ``'resident'``（居民）
        visit_type: ``'outpatient'`` / ``'inpatient'`` / ``'emergency'``
        hospital_level: 医院等级，不传则使用政策默认等级
        year_accumulated: 当年已累计报销金额（用于封顶线判断）
        policy_year: 使用哪一年的政策版本，默认取政策库的当前版本
        repository: 自定义政策仓库（测试或多套政策并存时使用）
    """

    def __init__(self, city_code: str, insurance_type: str, visit_type: str = 'inpatient',
                 hospital_level: Optional[str] = None, year_accumulated: float = 0, *,
                 policy_year: Optional[int] = None,
                 repository: Optional[PolicyRepository] = None):
        self.city_code = city_code
        self.insurance_type = insurance_type
        self.visit_type = visit_type
        self.hospital_level = hospital_level
        self.year_accumulated = year_accumulated

        self.repository = repository or get_repository()
        self.bundle = self.repository.load(policy_year)

        # 旧版属性：policy / reimburse_rates 保持可用
        self.policy = self.bundle.get_visit_policy(city_code, insurance_type, visit_type)
        if not self.policy:
            city_name = (self.bundle.cities.get(city_code) or {}).get('name', city_code)
            raise PolicyNotFoundError(
                '未找到 %s %s %s 的医保政策' % (city_name, insurance_type, visit_type),
                city=city_code, insurance_type=insurance_type, visit_type=visit_type,
            )

        self._init_rates()

        self._basic = BasicMedicalInsurance(self.bundle)
        self._serious = SeriousIllnessInsurance(self.bundle)
        self._chronic = ChronicDiseasePolicy(self.bundle)

    # ------------------------------------------------------------------
    def _init_rates(self) -> None:
        """确定医院等级并取出对应分段（旧版属性 ``reimburse_rates``）。"""
        self._requested_hospital_level = self.hospital_level
        level, tiers = resolve_tiers(self.policy, self.hospital_level)
        self.hospital_level = level
        self.reimburse_rates = tiers
        # 传了医院等级但政策里没有该等级时，会退化为默认等级
        self._hospital_level_fallback = bool(
            self._requested_hospital_level and self._requested_hospital_level != level)

    # ------------------------------------------------------------------
    def calculate(self, fee_items: Sequence[Any], *,
                  chronic_disease: Optional[str] = None,
                  certified: bool = True,
                  apply_serious_illness: bool = True) -> Dict[str, Any]:
        """计算报销金额。

        Args:
            fee_items: 费用清单，每项形如::

                    {'name': '西药费', 'amount': 200, 'category': 'class_b',
                     'item_type': 'drug'}

                ``category`` 可省略（按 ``item_type`` 推断），
                ``item_type`` 也可省略（默认 ``'other'``，按甲类处理）。
            chronic_disease: 病种代码。填了就按"门诊慢特病"待遇计算
                （如 ``'hypertension'`` / ``'diabetes'``）
            certified: 是否已通过慢特病认定备案（仅 ``chronic_disease`` 生效）
            apply_serious_illness: 是否接续计算大病保险，默认 ``True``

        Returns:
            dict: 计算结果。``summary`` / ``category_stats`` / ``basic_details`` /
            ``serious_illness`` / ``items_detail`` 与旧版结构一致，
            另附 ``formula`` / ``item_type_stats`` / ``policy`` / ``meta`` 等增强字段。

        Raises:
            PolicyNotFoundError: 政策不存在
            InvalidInputError: 费用明细格式非法
        """
        # 1) 统筹段：基本医保 或 门诊慢特病
        if chronic_disease:
            base_result = self._chronic.calculate(
                fee_items,
                city_code=self.city_code,
                disease_code=chronic_disease,
                insurance_type=self.insurance_type,
                certified=certified,
                year_accumulated=self.year_accumulated,
                hospital_level=self.hospital_level,
            )
            funding_source = 'chronic'
        else:
            base_result = self._basic.calculate(
                fee_items,
                city_code=self.city_code,
                insurance_type=self.insurance_type,
                visit_type=self.visit_type,
                hospital_level=self.hospital_level,
                year_accumulated=self.year_accumulated,
            )
            funding_source = 'basic'

        summary = base_result['summary']
        total_fee = summary['total_fee']
        basic_reimburse = summary['basic_reimburse']

        # 2) 个人自付（未计大病之前）
        personal_pay_before_serious = round_money(total_fee - basic_reimburse)

        # 3) 大病保险
        if apply_serious_illness:
            serious = self._serious.calculate(personal_pay_before_serious)
        else:
            serious = self._serious.calculate(0)
            serious['description'] = '本次不计算大病保险'

        total_reimburse = round_money(basic_reimburse + serious['reimburse'])
        final_personal_pay = round_money(total_fee - total_reimburse)

        summary.update({
            'serious_reimburse': serious['reimburse'],
            'total_reimburse': total_reimburse,
            'personal_pay': final_personal_pay,
            'reimburse_ratio': round_ratio(total_reimburse / total_fee * 100)
                               if total_fee > 0 else 0.0,
        })
        base_result['summary'] = summary

        # 4) 附加信息
        base_result['serious_illness'] = serious
        base_result['meta'] = self._build_meta(funding_source, chronic_disease)
        base_result['warnings'] = self._collect_warnings(base_result)

        return base_result

    # ------------------------------------------------------------------
    def calculate_chronic(self, fee_items: Sequence[Any], disease_code: str,
                          certified: bool = True) -> Dict[str, Any]:
        """按门诊慢特病待遇计算（``calculate`` 的语义化快捷方式）。"""
        return self.calculate(fee_items, chronic_disease=disease_code, certified=certified)

    def list_chronic_diseases(self, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """当前城市可用的门诊慢特病病种列表。"""
        return [
            dict(d, available_city=self.city_code)
            for d in self._chronic.list_diseases(level)
        ]

    def serious_illness_preview(self, personal_pay: float) -> Dict[str, Any]:
        """单独测算给定个人自付额下的大病保险报销。"""
        return self._serious.calculate(personal_pay)

    def describe_policy(self) -> Dict[str, Any]:
        """返回当前使用的政策摘要，便于前端展示"本次按哪版政策计算"。"""
        city = self.bundle.cities.get(self.city_code) or {}
        return {
            'city_code': self.city_code,
            'city_name': city.get('name', self.city_code),
            # 参考政策（如「全国通用」）带声明，展示结果时要一并说明
            'city_disclaimer': city.get('disclaimer', ''),
            'insurance_type': self.insurance_type,
            'visit_type': self.visit_type,
            'hospital_level': self.hospital_level,
            'deductible': self.policy.get('deductible'),
            'max_reimburse': self.policy.get('max_reimburse'),
            'tier_table': [
                {
                    'min': t['min'],
                    'max': t['max'],
                    'rate': t['rate'],
                    'rate_percent': round(t['rate'] * 100, 1),
                }
                for t in self.reimburse_rates
            ],
            'policy_year': self.bundle.year,
            'policy_version': self.bundle.version,
        }

    # ------------------------------------------------------------------
    def _build_meta(self, funding_source: str, chronic_disease: Optional[str]) -> Dict[str, Any]:
        """本次计算的元信息（政策版本、引擎版本、费用来源）。"""
        return {
            'engine_version': ENGINE_VERSION,
            'policy_year': self.bundle.year,
            'policy_version': self.bundle.version,
            'funding_source': funding_source,
            'chronic_disease': chronic_disease,
            'hospital_level': self.hospital_level,
            'requested_hospital_level': self._requested_hospital_level,
            'hospital_level_fallback': self._hospital_level_fallback,
        }

    def _collect_warnings(self, result: Mapping[str, Any]) -> List[str]:
        """收集需要提醒用户的边界情况。"""
        warnings: List[str] = []
        summary = result.get('summary', {})
        details = result.get('basic_details', {})

        if summary.get('total_fee', 0) <= 0:
            warnings.append('本次没有可计算的费用')
            return warnings

        if summary.get('reimburse_base', 0) <= 0:
            warnings.append('所有费用均为自费项目，不纳入报销基数')

        if details.get('capped_by_quota'):
            warnings.append(
                '本次报销已达到年度封顶线，超出部分需自付'
                '（本年度剩余额度 %s 元）' % details.get('remaining_quota'))

        deductible = summary.get('deductible', 0)
        if summary.get('above_deductible', 0) <= 0 < deductible:
            warnings.append('本次纳入报销的金额未超过起付线 %s 元，基本医保不予报销' % deductible)

        if not self.bundle.has_city(self.city_code):  # pragma: no cover - 构造时已拦截
            warnings.append('城市 %s 未收录，结果仅供参考' % self.city_code)

        return warnings


# --------------------------------------------------------------------------
# 快捷函数（签名与旧版一致）
# --------------------------------------------------------------------------
def quick_calculate(city_code: str, insurance_type: str, total_fee: float,
                    visit_type: str = 'inpatient', hospital_level: Optional[str] = None,
                    class_a_ratio: float = 0.6, class_b_ratio: float = 0.3,
                    self_pay_ratio: float = 0.1) -> Dict[str, Any]:
    """快速计算（简化版，按比例把总费用拆成甲类 / 乙类 / 自费）。

    Args:
        city_code: 城市代码
        insurance_type: 医保类型
        total_fee: 总费用
        visit_type: 就医类型
        hospital_level: 医院等级
        class_a_ratio: 甲类费用占比（默认 60%）
        class_b_ratio: 乙类费用占比（默认 30%）
        self_pay_ratio: 自费费用占比（默认 10%）

    Returns:
        dict: 计算结果，额外包含 ``explanation``（通俗解释文本）
    """
    fee_items = [
        {'name': '甲类费用', 'amount': total_fee * class_a_ratio,
         'category': 'class_a', 'item_type': 'other'},
        {'name': '乙类费用', 'amount': total_fee * class_b_ratio,
         'category': 'class_b', 'item_type': 'other'},
        {'name': '自费费用', 'amount': total_fee * self_pay_ratio,
         'category': 'self_pay', 'item_type': 'other'},
    ]

    calc = ReimbursementCalculator(city_code, insurance_type, visit_type, hospital_level)
    result = calc.calculate(fee_items)

    from .explain import generate_explanation  # 延迟导入，避免循环依赖
    result['explanation'] = generate_explanation(result)
    return result


def compare_cities(city_codes: Sequence[str], insurance_type: str, total_fee: float,
                   visit_type: str = 'inpatient',
                   hospital_level: Optional[str] = None) -> Dict[str, Any]:
    """同一笔费用在不同城市的报销对比。

    Args:
        city_codes: 城市代码序列

    Returns:
        ``{'rows': [...], 'best': {...}, 'worst': {...}}``，按报销金额降序
    """
    rows: List[Dict[str, Any]] = []
    for city_code in city_codes:
        try:
            result = quick_calculate(city_code, insurance_type, total_fee,
                                     visit_type, hospital_level)
        except PolicyNotFoundError as exc:
            rows.append({'city_code': city_code, 'error': str(exc)})
            continue
        rows.append({
            'city_code': city_code,
            'city_name': (result.get('policy') or {}).get('city_name', city_code),
            'total_reimburse': result['summary']['total_reimburse'],
            'personal_pay': result['summary']['personal_pay'],
            'reimburse_ratio': result['summary']['reimburse_ratio'],
        })

    valid = [r for r in rows if 'error' not in r]
    valid.sort(key=lambda r: r['total_reimburse'], reverse=True)
    return {
        'total_fee': total_fee,
        'insurance_type': insurance_type,
        'visit_type': visit_type,
        'rows': valid + [r for r in rows if 'error' in r],
        'best': valid[0] if valid else None,
        'worst': valid[-1] if valid else None,
        'spread': round_money(valid[0]['total_reimburse'] - valid[-1]['total_reimburse'])
        if len(valid) > 1 else 0.0,
    }
