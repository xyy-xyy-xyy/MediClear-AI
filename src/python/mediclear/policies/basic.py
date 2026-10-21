# -*- coding: utf-8 -*-
"""基本医疗保险计算模块（门诊 / 住院 / 急诊）。

计算链路（每一步都会在 ``formula`` 里留下可读的说明）::

    总费用
      ↓ 按报销类别折算（甲类 100%、乙类 80%、自费 0%）
    报销基数
      ↓ 扣除起付线
    可报销金额
      ↓ 按医院等级分段累进比例
    基本医保报销额
      ↓ 受年度封顶线约束
    实际基本报销额
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..constants import FEE_CLASS_CODES, ITEM_TYPES
from ..models import FeeItem, normalize_fee_items
from .base import (
    PolicyModule,
    TierTable,
    register_module,
    resolve_tiers,
    round_money,
    round_ratio,
)

__all__ = ['BasicMedicalInsurance']


def _tiered_detail(tiers: Sequence[Mapping[str, Any]], amount: float,
                   location: str, max_reimburse: float) -> Dict[str, Any]:
    """分段计算基本医保报销额，并生成公式说明。"""
    table = TierTable(tiers, name='基本医保报销比例')
    detail = table.breakdown(amount)

    # 依次累加的公式串，例如 "30000 × 85% + 10000 × 90% + 200 × 95%"
    expression = ' + '.join(
        '%s × %s%%' % (seg['amount'], round(seg['rate'] * 100, 2))
        for seg in detail['segments']
    ) or '0'

    detail.update({
        'hospital_level': location,
        'expression': expression,
        'raw_reimburse': round_money(detail['reimburse']),
        'max_reimburse': max_reimburse,
        'capped': detail['reimburse'] > max_reimburse,
    })
    return detail


@register_module
class BasicMedicalInsurance(PolicyModule):
    """基本医疗保险（统筹基金）报销计算。

    负责"报销基数 → 起付线 → 分段比例 → 封顶线"这条主链路，
    是其他所有险种（大病、慢特病、异地就医）的计算基础。
    """

    code = 'basic'
    name = '基本医疗保险'

    # ------------------------------------------------------------------
    def calculate(self, fee_items: Sequence[Any], *, city_code: str,
                  insurance_type: str, visit_type: str,
                  hospital_level: Optional[str] = None,
                  year_accumulated: float = 0) -> Dict[str, Any]:
        """计算基本医保报销。

        Args:
            fee_items: 费用明细（``dict`` 或 :class:`~mediclear.models.FeeItem` 列表）
            city_code: 城市代码
            insurance_type: 险种（``employee`` / ``resident``）
            visit_type: 就医类型（``outpatient`` / ``inpatient`` / ``emergency``）
            hospital_level: 医院等级，缺省用政策中的默认等级
            year_accumulated: 本年度**已报销**金额，用于计算封顶线剩余额度

        Returns:
            dict: 含 ``summary`` / ``basic_details`` / ``category_stats`` /
            ``item_type_stats`` / ``items_detail`` / ``formula`` 等键

        Raises:
            PolicyNotFoundError: 该城市 / 险种 / 就医类型没有政策
        """
        policy = self.bundle.require_visit_policy(city_code, insurance_type, visit_type)
        items = normalize_fee_items(fee_items)

        total_fee = round_money(sum(item.amount for item in items))
        category_stats = self._category_stats(items)
        item_type_stats = self._item_type_stats(items)
        reimburse_base = round_money(self._reimburse_base(items))

        level, tiers = resolve_tiers(policy, hospital_level)
        table = TierTable(tiers, name='基本医保报销比例')

        deductible = float(policy.get('deductible', 0) or 0)
        above_deductible = round_money(max(0.0, reimburse_base - deductible))

        tier_detail = _tiered_detail(tiers, above_deductible, level, 0) if above_deductible > 0 \
            else _empty_tier_detail(level, tiers)

        max_reimburse = float(policy.get('max_reimburse', 0) or 0)
        year_accumulated = max(0.0, float(year_accumulated or 0))
        remaining_quota = round_money(max(0.0, max_reimburse - year_accumulated))

        raw_reimburse = tier_detail['raw_reimburse']
        basic_reimburse = round_money(min(raw_reimburse, remaining_quota))
        capped_by_quota = raw_reimburse > remaining_quota

        personal_pay_before_serious = round_money(total_fee - basic_reimburse)

        summary = {
            'total_fee': total_fee,
            'reimburse_base': reimburse_base,
            'deductible': round_money(deductible),
            'above_deductible': above_deductible,
            'basic_reimburse': basic_reimburse,
            'serious_reimburse': 0.0,      # 由 engine 汇总时回填
            'total_reimburse': basic_reimburse,
            'personal_pay': personal_pay_before_serious,
            'reimburse_ratio': round_ratio(
                basic_reimburse / total_fee * 100) if total_fee > 0 else 0.0,
            'effective_reimburse_ratio': round_ratio(
                basic_reimburse / reimburse_base * 100) if reimburse_base > 0 else 0.0,
        }

        return {
            'summary': summary,
            'category_stats': category_stats,
            'item_type_stats': item_type_stats,
            'basic_details': {
                'deductible': round_money(deductible),
                'max_reimburse': max_reimburse,
                'year_accumulated': round_money(year_accumulated),
                'remaining_quota': remaining_quota,
                'hospital_level': level,
                'reimburse_rates': list(tiers),
                'tier_table': table.describe(),
                'tier_breakdown': tier_detail,
                'capped_by_quota': capped_by_quota,
                'quota_used_after': round_money(year_accumulated + basic_reimburse),
            },
            'items_detail': self._items_detail(items),
            'formula': self._build_formula(
                total_fee, reimburse_base, deductible, above_deductible,
                tier_detail, raw_reimburse, remaining_quota, basic_reimburse,
                capped_by_quota, level),
            'policy': {
                'city_code': city_code,
                'city_name': (self.bundle.cities.get(city_code) or {}).get('name', city_code),
                # 参考政策（如「全国通用」）带声明，展示结果时要一并说明
                'city_disclaimer': (self.bundle.cities.get(city_code) or {}).get('disclaimer', ''),
                'insurance_type': insurance_type,
                'visit_type': visit_type,
                'hospital_level': level,
                'policy_year': self.bundle.year,
                'policy_version': self.bundle.version,
            },
        }

    # ------------------------------------------------------------------
    # 分项统计
    # ------------------------------------------------------------------
    def _category_stats(self, items: Sequence[FeeItem]) -> Dict[str, Dict[str, Any]]:
        """按报销类别（甲/乙/自费）统计金额与笔数。"""
        categories = self.bundle.fee_categories
        stats = {
            code: {
                'name': (categories.get(code) or {}).get('name', code),
                'total': 0.0,
                'count': 0,
                'reimburse_ratio_base': (categories.get(code) or {}).get('reimburse_ratio_base', 0),
            }
            for code in FEE_CLASS_CODES
        }
        for item in items:
            bucket = stats.get(item.category)
            if bucket is None:  # 模型层已保证不会走到这里，防御性兜底
                continue
            bucket['total'] = round_money(bucket['total'] + item.amount)
            bucket['count'] += 1

        # 附加占比，便于前端直接画饼图
        total = sum(b['total'] for b in stats.values())
        for bucket in stats.values():
            bucket['ratio'] = round(bucket['total'] / total * 100, 1) if total > 0 else 0.0
        return stats

    def _item_type_stats(self, items: Sequence[FeeItem]) -> Dict[str, Dict[str, Any]]:
        """按费用项目类型（药品/检查/治疗/耗材...）统计。"""
        stats: Dict[str, Dict[str, Any]] = {}
        for item in items:
            bucket = stats.setdefault(item.item_type, {
                'name': ITEM_TYPES.get(item.item_type, ITEM_TYPES['other'])['name'],
                'total': 0.0,
                'count': 0,
                'reimburse_base': 0.0,
            })
            bucket['total'] = round_money(bucket['total'] + item.amount)
            bucket['count'] += 1
            base_ratio = self._class_ratio(item.category)
            bucket['reimburse_base'] = round_money(
                bucket['reimburse_base'] + item.amount * base_ratio)

        total = sum(b['total'] for b in stats.values())
        for bucket in stats.values():
            bucket['ratio'] = round(bucket['total'] / total * 100, 1) if total > 0 else 0.0
        return stats

    def _class_ratio(self, category: str) -> float:
        """某报销类别纳入报销基数的比例（甲 1.0 / 乙 0.8 / 自费 0）。"""
        return float(
            (self.bundle.fee_categories.get(category) or {}).get('reimburse_ratio_base', 0.0))

    def _reimburse_base(self, items: Sequence[FeeItem]) -> float:
        """计算纳入报销基数的金额。"""
        return sum(item.amount * self._class_ratio(item.category) for item in items)

    def _items_detail(self, items: Sequence[FeeItem]) -> List[Dict[str, Any]]:
        """逐项明细：金额、类别、类型、计入基数、自付部分。"""
        details: List[Dict[str, Any]] = []
        for item in items:
            base_ratio = self._class_ratio(item.category)
            base_amount = item.amount * base_ratio
            details.append({
                'name': item.name,
                'amount': item.amount,
                'category': item.category,
                'category_name': (self.bundle.fee_categories.get(item.category) or {})
                                 .get('name', item.category),
                'item_type': item.item_type,
                'item_type_name': item.item_type_name,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'reimburse_ratio_base': base_ratio,
                'reimburse_base': round_money(base_amount),
                'self_pay_part': round_money(item.amount - base_amount),
                'note': item.note,
            })
        return details

    # ------------------------------------------------------------------
    # 公式说明
    # ------------------------------------------------------------------
    def _build_formula(self, total_fee, reimburse_base, deductible, above_deductible,
                       tier_detail, raw_reimburse, remaining_quota, basic_reimburse,
                       capped_by_quota, level) -> Dict[str, Any]:
        """把计算过程整理成"人话 + 公式"两份说明。"""
        steps = [
            {
                'key': 'total_fee',
                'title': '医疗总费用',
                'expression': '各项目金额相加 = %s 元' % total_fee,
                'result': total_fee,
            },
            {
                'key': 'reimburse_base',
                'title': '纳入报销基数',
                'expression': '甲类全额 + 乙类按 %s%% + 自费不计入 = %s 元' % (
                    round(self._class_ratio('class_b') * 100, 2), reimburse_base),
                'result': reimburse_base,
            },
            {
                'key': 'above_deductible',
                'title': '超过起付线部分',
                'expression': 'max(0, %s - %s) = %s 元' % (
                    reimburse_base, deductible, above_deductible),
                'result': above_deductible,
            },
            {
                'key': 'tier_reimburse',
                'title': '%s 分段报销' % level,
                'expression': '%s = %s 元' % (tier_detail.get('expression', '0'), raw_reimburse),
                'result': raw_reimburse,
            },
            {
                'key': 'basic_reimburse',
                'title': '基本医保实际报销',
                'expression': 'min(%s, 封顶线剩余 %s) = %s 元' % (
                    raw_reimburse, remaining_quota, basic_reimburse),
                'result': basic_reimburse,
                'note': '已达年度封顶线，超出部分不报销' if capped_by_quota else '',
            },
        ]

        text_lines = ['【基本医保计算过程】']
        for index, step in enumerate(steps, 1):
            text_lines.append('%d. %s：%s' % (index, step['title'], step['expression']))
            if step.get('note'):
                text_lines.append('   ⚠️ %s' % step['note'])

        return {
            'steps': steps,
            'text': '\n'.join(text_lines),
            'effective_rate_of_base': tier_detail.get('effective_rate', 0.0),
            'top_rate': tier_detail.get('top_rate', 0.0),
        }


def _empty_tier_detail(location: str, tiers: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """费用未超过起付线时的空分段结果。"""
    return {
        'name': '基本医保报销比例',
        'location': location,
        'base': 0.0,
        'reimburse': 0.0,
        'raw_reimburse': 0.0,
        'effective_rate': 0.0,
        'top_rate': 0.0,
        'expression': '0',
        'hospital_level': location,
        'max_reimburse': 0,
        'capped': False,
        'segments': [],
    }
