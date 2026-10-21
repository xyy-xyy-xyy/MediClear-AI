# -*- coding: utf-8 -*-
"""对比分析：不同城市、不同医院等级、不同方案之间的报销差异。

所有对比都归结为一个通用入口 :meth:`ComparisonAnalyzer.compare_scenarios`，
其余方法只是它的语义化封装：

* :meth:`compare_cities` —— 同样的花费，各城市能报多少
* :meth:`compare_hospital_levels` —— 同一城市，一级 / 二级 / 三级医院差异
* :meth:`compare_insurance_types` —— 职工医保 vs 居民医保
* :meth:`compare_chronic_certification` —— 办不办慢特病认定差多少
* :meth:`compare_relocation` —— 异地就医备案前后的差距
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..constants import EMPLOYEE, INPATIENT, OUTPATIENT, RESIDENT
from ..data.repository import PolicyBundle
from ..exceptions import InvalidInputError, PolicyNotFoundError
from ..policies.base import round_money
from ..engine import ReimbursementCalculator

__all__ = ['ComparisonAnalyzer']

DEFAULT_CLASS_RATIOS = {'class_a': 0.6, 'class_b': 0.3, 'self_pay': 0.1}


class ComparisonAnalyzer:
    """多方案对比分析器。

    Args:
        bundle: 政策数据包；用于取城市名称等元信息
    """

    def __init__(self, bundle: PolicyBundle):
        self.bundle = bundle

    # ------------------------------------------------------------------
    def compare_scenarios(self, scenarios: Sequence[Mapping[str, Any]], *,
                          class_ratios: Optional[Mapping[str, float]] = None,
                          sort_by: str = 'total_reimburse') -> Dict[str, Any]:
        """通用对比入口：把每个方案各算一遍并排名。

        Args:
            scenarios: 方案列表，每项支持以下键::

                    {'label': '北京-职工', 'city_code': 'beijing',
                     'insurance_type': 'employee', 'amount': 30000,
                     'visit_type': 'inpatient', 'hospital_level': '三级医院',
                     'chronic_disease': None, 'year_accumulated': 0}

                ``amount`` 必填，其余可选（默认职工医保 / 住院 / 默认等级）。
            class_ratios: 甲 / 乙 / 自费占比，默认 60% / 30% / 10%
            sort_by: 排序字段，``'total_reimburse'``（默认，降序）
                或 ``'personal_pay'``（升序）

        Returns:
            ``{'rows', 'best', 'worst', 'spread', 'baseline', 'comparisons'}``

        Raises:
            InvalidInputError: 方案列表为空或金额非法
        """
        scenarios = list(scenarios or [])
        if not scenarios:
            raise InvalidInputError('至少需要一个对比方案')

        ratios = dict(DEFAULT_CLASS_RATIOS)
        ratios.update(class_ratios or {})

        rows: List[Dict[str, Any]] = []
        for index, raw in enumerate(scenarios, start=1):
            label = raw.get('label') or '方案%d' % index
            row = self._run_scenario(raw, label, ratios)
            rows.append(row)

        valid = [r for r in rows if 'error' not in r]
        if not valid:
            raise InvalidInputError('所有方案都无法计算：%s'
                                    % '；'.join(r['error'] for r in rows))

        if sort_by == 'personal_pay':
            valid.sort(key=lambda r: r['personal_pay'])
        else:
            valid.sort(key=lambda r: r['total_reimburse'], reverse=True)
        rows = valid + [r for r in rows if 'error' in r]

        best, worst = rows[0], valid[-1]
        baseline = valid[0]

        comparisons = [
            {
                'label': row['label'],
                'total_reimburse_diff': round_money(
                    row['total_reimburse'] - baseline['total_reimburse']),
                'personal_pay_diff': round_money(row['personal_pay'] - baseline['personal_pay']),
                'ratio_diff': round(row['reimburse_ratio'] - baseline['reimburse_ratio'], 1),
                'verdict': self._verdict(row, baseline),
            }
            for row in valid
        ]

        return {
            'rows': rows,
            'best': best,
            'worst': worst,
            'baseline': baseline,
            'spread': round_money(best['total_reimburse'] - worst['total_reimburse']),
            'comparisons': comparisons,
            'sort_by': sort_by,
            'class_ratios': ratios,
            'summary': self._summary_text(best, worst),
        }

    # ------------------------------------------------------------------
    def compare_cities(self, city_codes: Sequence[str], amount: float, *,
                       insurance_type: str = EMPLOYEE, visit_type: str = INPATIENT,
                       hospital_level: Optional[str] = None,
                       class_ratios: Optional[Mapping[str, float]] = None) -> Dict[str, Any]:
        """同一笔费用在不同城市的报销对比。"""
        scenarios = [
            {
                'label': (self.bundle.cities.get(code) or {}).get('name', code),
                'city_code': code,
                'insurance_type': insurance_type,
                'visit_type': visit_type,
                'hospital_level': hospital_level,
                'amount': amount,
            }
            for code in city_codes
        ]
        result = self.compare_scenarios(scenarios, class_ratios=class_ratios)
        result['dimension'] = 'city'
        return result

    def compare_hospital_levels(self, city_code: str, amount: float, *,
                                insurance_type: str = EMPLOYEE,
                                visit_type: str = INPATIENT,
                                levels: Optional[Sequence[str]] = None,
                                class_ratios: Optional[Mapping[str, float]] = None) -> Dict[str, Any]:
        """同一城市不同医院等级的报销对比。"""
        levels = list(levels or self.bundle.hospital_levels or
                      ['一级医院', '二级医院', '三级医院'])
        scenarios = [
            {
                'label': level,
                'city_code': city_code,
                'insurance_type': insurance_type,
                'visit_type': visit_type,
                'hospital_level': level,
                'amount': amount,
            }
            for level in levels
        ]
        result = self.compare_scenarios(scenarios, class_ratios=class_ratios)
        result['dimension'] = 'hospital_level'
        result['city_code'] = city_code
        return result

    def compare_insurance_types(self, city_code: str, amount: float, *,
                                visit_type: str = INPATIENT,
                                hospital_level: Optional[str] = None,
                                class_ratios: Optional[Mapping[str, float]] = None) -> Dict[str, Any]:
        """职工医保与居民医保的报销对比。"""
        names = {EMPLOYEE: '职工医保', RESIDENT: '居民医保'}
        scenarios = [
            {
                'label': names[code],
                'city_code': city_code,
                'insurance_type': code,
                'visit_type': visit_type,
                'hospital_level': hospital_level,
                'amount': amount,
            }
            for code in (EMPLOYEE, RESIDENT)
            if code in self.bundle.supported_insurance_types(city_code)
        ]
        result = self.compare_scenarios(scenarios, class_ratios=class_ratios)
        result['dimension'] = 'insurance_type'
        result['city_code'] = city_code
        return result

    # ------------------------------------------------------------------
    def compare_chronic_certification(self, city_code: str, disease_code: str,
                                      amount: float, *,
                                      insurance_type: str = EMPLOYEE,
                                      hospital_level: Optional[str] = None,
                                      class_ratios: Optional[Mapping[str, float]] = None) -> Dict[str, Any]:
        """对比"办理慢特病认定"与"按普通门诊"的报销差距。

        Returns:
            对比结果，另附 ``extra_reimburse``（认定后多报销的金额）
        """
        disease = self.bundle.resolve_chronic_disease(disease_code, city_code)
        scenarios = [
            {
                'label': '普通门诊',
                'city_code': city_code,
                'insurance_type': insurance_type,
                'visit_type': OUTPATIENT,
                'hospital_level': hospital_level,
                'amount': amount,
            },
            {
                'label': '已认定「%s」' % disease['name'],
                'city_code': city_code,
                'insurance_type': insurance_type,
                'visit_type': OUTPATIENT,
                'hospital_level': hospital_level,
                'amount': amount,
                'chronic_disease': disease_code,
                'certified': True,
            },
        ]
        try:
            result = self.compare_scenarios(scenarios, class_ratios=class_ratios)
        except InvalidInputError:
            # 未认定时慢特病不可用，只保留普通门诊方案
            result = self.compare_scenarios(scenarios[:1], class_ratios=class_ratios)

        plain = next((r for r in result['rows'] if r['label'] == '普通门诊'), None)
        certified = next((r for r in result['rows'] if r['label'] != '普通门诊'), None)
        result['dimension'] = 'chronic'
        result['disease'] = disease
        result['extra_reimburse'] = round_money(
            (certified or {}).get('total_reimburse', 0) - (plain or {}).get('total_reimburse', 0))
        if result['extra_reimburse'] > 0:
            result['summary'] = ('办理「%s」门诊慢特病认定后，同样的费用可多报销约 %s 元。'
                                 % (disease['name'], result['extra_reimburse']))
        return result

    def compare_relocation(self, insured_city: str, visit_city: str, amount: float, *,
                           insurance_type: str = EMPLOYEE, visit_type: str = INPATIENT,
                           hospital_level: Optional[str] = None,
                           record_types: Sequence[str] = ('long_term', 'temp',
                                                          'transfer', 'none')) -> Dict[str, Any]:
        """异地就医不同备案类型的报销对比。

        依赖 ``relocation`` 模块；未导入时给出友好提示。
        """
        try:
            from relocation import RelocationCalculator  # 兼容顶层模块
        except ImportError as exc:  # pragma: no cover - 环境异常
            raise InvalidInputError('异地就医模块不可用：%s' % exc)

        labels = {
            'long_term': '长期驻外备案',
            'temp': '临时外出备案',
            'transfer': '转诊转院备案',
            'none': '未备案',
        }

        rows: List[Dict[str, Any]] = []
        for record_type in record_types:
            try:
                calc = RelocationCalculator(insured_city, visit_city, insurance_type,
                                            visit_type, record_type)
                result = calc.calculate(amount, hospital_level or '三级医院')
            except (PolicyNotFoundError, ValueError) as exc:
                rows.append({'label': labels.get(record_type, record_type), 'error': str(exc)})
                continue

            summary = result['summary']
            rows.append({
                'label': labels.get(record_type, record_type),
                'record_type': record_type,
                'total_reimburse': summary['totalReimburse'],
                'personal_pay': summary['personalPay'],
                'reimburse_ratio': summary['reimburseRatio'],
                'final_rate': summary['finalRate'],
            })

        valid = [r for r in rows if 'error' not in r]
        valid.sort(key=lambda r: r['total_reimburse'], reverse=True)

        best = valid[0] if valid else None
        worst = valid[-1] if valid else None
        return {
            'dimension': 'relocation',
            'insured_city': insured_city,
            'visit_city': visit_city,
            'rows': valid + [r for r in rows if 'error' in r],
            'best': best,
            'worst': worst,
            'spread': round_money(best['total_reimburse'] - worst['total_reimburse'])
            if best and worst else 0.0,
            'summary': ('备案类型对报销影响很大：' +
                        ('不同备案方式之间最多相差约 %s 元。'
                         % round_money(best['total_reimburse'] - worst['total_reimburse'])
                         if best and worst and len(valid) > 1 else '请先办理备案。'))
            if best and worst else '',
        }

    # ------------------------------------------------------------------
    def _run_scenario(self, raw: Mapping[str, Any], label: str,
                      ratios: Mapping[str, float]) -> Dict[str, Any]:
        """执行单个方案并整理成对比表格的一行。"""
        amount = raw.get('amount')
        if amount is None or float(amount) < 0:
            return {'label': label, 'error': '缺少合法的 amount'}

        city_code = raw.get('city_code', '')
        insurance_type = raw.get('insurance_type', EMPLOYEE)
        visit_type = raw.get('visit_type', INPATIENT)
        hospital_level = raw.get('hospital_level')
        chronic_disease = raw.get('chronic_disease')

        fee_items = [
            {'name': '甲类费用', 'amount': float(amount) * ratios['class_a'],
             'category': 'class_a', 'item_type': 'other'},
            {'name': '乙类费用', 'amount': float(amount) * ratios['class_b'],
             'category': 'class_b', 'item_type': 'other'},
            {'name': '自费费用', 'amount': float(amount) * ratios['self_pay'],
             'category': 'self_pay', 'item_type': 'other'},
        ]

        try:
            calc = ReimbursementCalculator(
                city_code, insurance_type, visit_type, hospital_level,
                raw.get('year_accumulated', 0) or 0,
                policy_year=self.bundle.year)
            result = calc.calculate(fee_items,
                                    chronic_disease=chronic_disease,
                                    certified=raw.get('certified', True))
        except (PolicyNotFoundError, ValueError, InvalidInputError) as exc:
            return {'label': label, 'error': str(exc)}

        summary = result['summary']
        return {
            'label': label,
            'city_code': city_code,
            'city_name': (self.bundle.cities.get(city_code) or {}).get('name', city_code),
            'insurance_type': insurance_type,
            'visit_type': visit_type,
            # 慢特病未认定等情况下没有 basic_details，回落到请求的等级
            'hospital_level': (hospital_level
                               or (result.get('basic_details') or {}).get('hospital_level', '')),
            'amount': round_money(float(amount)),
            'deductible': summary['deductible'],
            'reimburse_base': summary['reimburse_base'],
            'basic_reimburse': summary['basic_reimburse'],
            'serious_reimburse': summary['serious_reimburse'],
            'total_reimburse': summary['total_reimburse'],
            'personal_pay': summary['personal_pay'],
            'reimburse_ratio': summary['reimburse_ratio'],
        }

    @staticmethod
    def _verdict(row: Mapping[str, Any], baseline: Mapping[str, Any]) -> str:
        """用一句话描述这一行相对基准的表现。"""
        diff = round_money(row['total_reimburse'] - baseline['total_reimburse'])
        if abs(diff) < 0.005:
            return '与基准方案基本持平'
        if diff > 0:
            return '比基准方案多报销 %s 元' % diff
        return '比基准方案少报销 %s 元' % abs(diff)

    @staticmethod
    def _summary_text(best: Mapping[str, Any], worst: Mapping[str, Any]) -> str:
        """对比结论。"""
        if best.get('label') == worst.get('label'):
            return '只有一个方案可比较。'
        return ('「%s」报销最多（%s 元，比例 %s%%），'
                '「%s」报销最少（%s 元），两者相差 %s 元。'
                % (best['label'], best['total_reimburse'], best['reimburse_ratio'],
                   worst['label'], worst['total_reimburse'],
                   round_money(best['total_reimburse'] - worst['total_reimburse'])))
