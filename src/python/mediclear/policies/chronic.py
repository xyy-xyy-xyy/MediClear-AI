# -*- coding: utf-8 -*-
"""门诊慢特病（门诊慢性病 / 门诊特殊疾病）计算模块。

与普通门诊的区别：

* 需要**先认定备案**（病种认定通过后才能享受），未认定的按普通门诊算；
* 报销比例更高、起付线更低（常见为 0）；
* 有**独立的年度限额**，与住院封顶线互不占用。

各病种的默认待遇在城市政策不一致时，由 ``extended.json`` 里的
``chronic_diseases.diseases.<病种>.cities.<城市>`` 覆盖。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..constants import CHRONIC_LEVEL_COMMON
from ..exceptions import PolicyNotFoundError
from ..models import FeeItem, normalize_fee_items
from .base import PolicyModule, register_module, round_money, round_ratio

__all__ = ['ChronicDiseasePolicy']


@register_module
class ChronicDiseasePolicy(PolicyModule):
    """门诊慢特病待遇计算。"""

    code = 'chronic'
    name = '门诊慢特病'

    def enabled(self) -> bool:
        """慢特病政策是否启用。"""
        return bool(self.bundle.chronic_diseases.get('enabled'))

    # ------------------------------------------------------------------
    def list_diseases(self, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出病种。

        Args:
            level: ``'慢性病'`` / ``'特殊疾病'``，``None`` 表示全部
        """
        diseases = self.bundle.list_chronic_diseases()
        if level:
            diseases = [d for d in diseases if d['level'] == level]
        return diseases

    def resolve(self, disease_code: str, city_code: str) -> Dict[str, Any]:
        """获取某病种在某城市的实际待遇（默认值 + 城市覆盖）。

        Raises:
            PolicyNotFoundError: 病种或城市不存在
        """
        if not self.bundle.has_city(city_code):
            raise PolicyNotFoundError('未收录城市 %r' % city_code, city=city_code)
        return self.bundle.resolve_chronic_disease(disease_code, city_code)

    def find_disease(self, keyword: str) -> List[Dict[str, Any]]:
        """按名称关键字搜索病种（支持中文模糊匹配）。"""
        keyword = (keyword or '').strip()
        if not keyword:
            return self.list_diseases()
        return [
            d for d in self.list_diseases()
            if keyword in d['name'] or keyword in d['code']
        ]

    # ------------------------------------------------------------------
    def calculate(self, fee_items: Sequence[Any], *, city_code: str,
                  disease_code: str, insurance_type: str = 'employee',
                  certified: bool = True, year_accumulated: float = 0,
                  hospital_level: Optional[str] = None) -> Dict[str, Any]:
        """计算门诊慢特病报销。

        Args:
            fee_items: 费用明细
            city_code: 参保城市
            disease_code: 病种代码，如 ``'hypertension'``
            insurance_type: 险种
            certified: 是否已通过慢特病认定备案
            year_accumulated: 该病种本年度已报销金额（占用年度限额）
            hospital_level: 医院等级（当前待遇按病种统一比例，保留参数便于扩展）

        Returns:
            dict: 结构对齐 ``BasicMedicalInsurance``，便于引擎统一接续大病保险
        """
        disease = self.resolve(disease_code, city_code)
        items = normalize_fee_items(fee_items)

        total_fee = round_money(sum(item.amount for item in items))
        reimburse_base = round_money(self._reimburse_base(items))

        if not self.enabled():
            return self._blocked(
                'disabled', '当前政策版本未启用门诊慢特病待遇',
                total_fee, reimburse_base, disease, items)

        if not certified:
            return self._blocked(
                'not_certified',
                '尚未办理「%s」门诊慢特病认定备案，本次费用按普通门诊政策结算' % disease['name'],
                total_fee, reimburse_base, disease, items)

        deductible = float(disease.get('deductible', 0) or 0)
        rate = float(disease.get('rate', 0) or 0)
        annual_quota = float(disease.get('annual_quota', 0) or 0)

        above_deductible = round_money(max(0.0, reimburse_base - deductible))
        raw_reimburse = round_money(above_deductible * rate)

        year_accumulated = max(0.0, float(year_accumulated or 0))
        remaining_quota = round_money(max(0.0, annual_quota - year_accumulated))
        reimburse = round_money(min(raw_reimburse, remaining_quota))
        capped = raw_reimburse > remaining_quota

        personal_pay = round_money(total_fee - reimburse)

        formula_text = (
            '【门诊慢特病计算过程】\n'
            '1. 病种：%s（%s），已认定备案\n'
            '2. 纳入报销基数 %s 元，起付线 %s 元，超过部分 %s 元\n'
            '3. %s × %s%% = %s 元\n'
            '4. 年度限额 %s 元（已用 %s 元），本次报销 %s 元'
        ) % (disease['name'], disease['level'], reimburse_base, deductible,
             above_deductible, above_deductible, round(rate * 100, 1),
             raw_reimburse, annual_quota, year_accumulated, reimburse)

        return {
            'summary': {
                'total_fee': total_fee,
                'reimburse_base': reimburse_base,
                'deductible': round_money(deductible),
                'above_deductible': above_deductible,
                'basic_reimburse': reimburse,
                'serious_reimburse': 0.0,
                'total_reimburse': reimburse,
                'personal_pay': personal_pay,
                'reimburse_ratio': round_ratio(reimburse / total_fee * 100) if total_fee else 0.0,
                'effective_reimburse_ratio': round_ratio(
                    reimburse / reimburse_base * 100) if reimburse_base else 0.0,
            },
            'chronic_details': {
                'disease_code': disease['code'],
                'disease_name': disease['name'],
                'level': disease.get('level', CHRONIC_LEVEL_COMMON),
                'rate': rate,
                'rate_percent': round(rate * 100, 1),
                'deductible': round_money(deductible),
                'annual_quota': annual_quota,
                'year_accumulated': round_money(year_accumulated),
                'remaining_quota': remaining_quota,
                'capped_by_quota': capped,
                'is_city_override': disease.get('is_city_override', False),
                'certified': True,
            },
            'category_stats': self._category_stats(items),
            'items_detail': self._items_detail(items),
            'formula': {
                'steps': [
                    {'key': 'reimburse_base', 'title': '纳入报销基数',
                     'expression': '%s 元' % reimburse_base, 'result': reimburse_base},
                    {'key': 'above_deductible', 'title': '超过起付线部分',
                     'expression': 'max(0, %s - %s) = %s 元'
                                   % (reimburse_base, deductible, above_deductible),
                     'result': above_deductible},
                    {'key': 'raw_reimburse', 'title': '%s 报销' % disease['name'],
                     'expression': '%s × %s%% = %s 元'
                                   % (above_deductible, round(rate * 100, 1), raw_reimburse),
                     'result': raw_reimburse},
                    {'key': 'reimburse', 'title': '限额内实际报销',
                     'expression': 'min(%s, 限额剩余 %s) = %s 元'
                                   % (raw_reimburse, remaining_quota, reimburse),
                     'result': reimburse,
                     'note': '已达该病种年度限额' if capped else ''},
                ],
                'text': formula_text,
            },
            'policy': {
                'city_code': city_code,
                'insurance_type': insurance_type,
                'visit_type': 'chronic',
                'policy_year': self.bundle.year,
                'policy_version': self.bundle.version,
            },
            'eligible': True,
            'blocked_reason': '',
            'message': '',
        }

    # ------------------------------------------------------------------
    def _blocked(self, reason: str, message: str, total_fee: float,
                 reimburse_base: float, disease: Dict[str, Any],
                 items: Sequence[FeeItem]) -> Dict[str, Any]:
        """无法享受慢特病待遇时的结果（未启用 / 未认定）。"""
        return {
            'summary': {
                'total_fee': total_fee,
                'reimburse_base': reimburse_base,
                'deductible': 0.0,
                'above_deductible': reimburse_base,
                'basic_reimburse': 0.0,
                'serious_reimburse': 0.0,
                'total_reimburse': 0.0,
                'personal_pay': total_fee,
                'reimburse_ratio': 0.0,
                'effective_reimburse_ratio': 0.0,
            },
            'chronic_details': {
                'disease_code': disease.get('code'),
                'disease_name': disease.get('name'),
                'level': disease.get('level', CHRONIC_LEVEL_COMMON),
                'certified': reason != 'not_certified',
            },
            'category_stats': self._category_stats(items),
            'items_detail': self._items_detail(items),
            'formula': {'steps': [], 'text': message},
            'policy': {
                'city_code': None,
                'insurance_type': None,
                'visit_type': 'chronic',
                'policy_year': self.bundle.year,
                'policy_version': self.bundle.version,
            },
            'eligible': False,
            'blocked_reason': reason,
            'message': message,
        }

    def _class_ratio(self, category: str) -> float:
        return float(
            (self.bundle.fee_categories.get(category) or {}).get('reimburse_ratio_base', 0.0))

    def _reimburse_base(self, items: Sequence[FeeItem]) -> float:
        return sum(item.amount * self._class_ratio(item.category) for item in items)

    def _category_stats(self, items: Sequence[FeeItem]) -> Dict[str, Dict[str, Any]]:
        stats: Dict[str, Dict[str, Any]] = {}
        for item in items:
            bucket = stats.setdefault(item.category, {
                'name': (self.bundle.fee_categories.get(item.category) or {})
                        .get('name', item.category),
                'total': 0.0,
                'count': 0,
            })
            bucket['total'] = round_money(bucket['total'] + item.amount)
            bucket['count'] += 1
        return stats

    def _items_detail(self, items: Sequence[FeeItem]) -> List[Dict[str, Any]]:
        return [
            {
                'name': item.name,
                'amount': item.amount,
                'category': item.category,
                'category_name': (self.bundle.fee_categories.get(item.category) or {})
                                 .get('name', item.category),
                'item_type': item.item_type,
                'item_type_name': item.item_type_name,
                'reimburse_base': round_money(item.amount * self._class_ratio(item.category)),
                'self_pay_part': round_money(
                    item.amount - item.amount * self._class_ratio(item.category)),
            }
            for item in items
        ]

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info['disease_count'] = len(self.list_diseases())
        info['levels'] = sorted({d['level'] for d in self.list_diseases()})
        return info
