# -*- coding: utf-8 -*-
"""大病保险计算模块。

大病保险对"个人自付"超过大病起付线的部分再报销一次，
是对基本医保的二次减负，通常还有自己的年度封顶线。

计算口径（本引擎采用的口径，与政策 JSON 中 ``serious_illness`` 对应）::

    个人自付 = 总费用 - 基本医保报销
    大病报销 = (个人自付 - 大病起付线) × 报销比例，且不超过大病封顶线

支持两种比例形态：

* **单一比例**：政策里只给 ``reimburse_rate``（当前各城市用的形态）
* **分段比例**：政策里给了 ``reimburse_rates`` 时按超额累进计算
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..constants import RATIO_PRECISION
from .base import PolicyModule, TierTable, register_module, round_money, round_ratio

__all__ = ['SeriousIllnessInsurance']


@register_module
class SeriousIllnessInsurance(PolicyModule):
    """大病保险（城乡居民大病保险 / 职工大额医疗补助）。"""

    code = 'serious_illness'
    name = '大病保险'

    @property
    def policy(self) -> Dict[str, Any]:
        """当前政策版本的大病保险配置。"""
        return self.bundle.serious_illness

    def enabled(self) -> bool:
        """大病保险是否启用。"""
        return bool(self.policy.get('enabled'))

    # ------------------------------------------------------------------
    def calculate(self, personal_pay: float, *,
                  year_accumulated: float = 0) -> Dict[str, Any]:
        """计算大病保险报销额。

        Args:
            personal_pay: 本次就医的个人自付金额（总费用 - 基本医保报销）
            year_accumulated: 本年度已报销的大病金额，用于封顶线判断

        Returns:
            dict: 与旧版 ``_calc_serious_illness`` 返回结构兼容，
            并额外提供 ``segments`` / ``formula`` / ``remaining_quota``
        """
        policy = self.policy

        if not self.enabled():
            return {
                'reimburse': 0.0,
                'raw_reimburse': 0.0,
                'eligible': False,
                'deductible': 0.0,
                'above_deductible': 0.0,
                'rate': 0.0,
                'max_reimburse': 0.0,
                'remaining_quota': 0.0,
                'capped': False,
                'segments': [],
                'description': '未纳入大病保险',
                'formula': {'steps': [], 'text': '当前政策版本未启用大病保险。'},
            }

        deductible = float(policy.get('deductible', 0) or 0)
        max_reimburse = float(policy.get('max_reimburse', 0) or 0)
        personal_pay = max(0.0, float(personal_pay or 0))
        above_deductible = round_money(max(0.0, personal_pay - deductible))

        year_accumulated = max(0.0, float(year_accumulated or 0))
        remaining_quota = round_money(max(0.0, max_reimburse - year_accumulated))

        segments, raw_reimburse, rate = self._reimburse_by_rate(above_deductible, policy)
        reimburse = round_money(min(raw_reimburse, remaining_quota))
        capped = raw_reimburse > remaining_quota

        expression = ' × '.join([
            '%s' % above_deductible,
            '%s%%' % round(rate * 100, RATIO_PRECISION),
        ]) if not segments else ' + '.join(
            '%s × %s%%' % (seg['amount'], round(seg['rate'] * 100, 2)) for seg in segments)

        formula = {
            'steps': [
                {
                    'key': 'above_deductible',
                    'title': '超过大病起付线部分',
                    'expression': 'max(0, %s - %s) = %s 元' % (
                        round_money(personal_pay), deductible, above_deductible),
                    'result': above_deductible,
                },
                {
                    'key': 'serious_reimburse',
                    'title': '大病保险报销',
                    'expression': 'min(%s, 封顶线剩余 %s) = %s 元' % (
                        expression or '0', remaining_quota, reimburse),
                    'result': reimburse,
                    'note': '已达大病保险年度封顶线' if capped else '',
                },
            ],
            'text': (
                '【大病保险计算过程】\n'
                '1. 个人自付 %s 元，大病起付线 %s 元，超过部分 %s 元\n'
                '2. %s = %s 元（封顶线 %s 元）'
            ) % (round_money(personal_pay), deductible, above_deductible,
                 expression or '0', reimburse, max_reimburse),
        }

        return {
            'reimburse': reimburse,
            'raw_reimburse': round_money(raw_reimburse),
            'eligible': above_deductible > 0 and reimburse > 0,
            'deductible': round_money(deductible),
            'above_deductible': above_deductible,
            'rate': round(rate, 4),
            'rate_percent': round(rate * 100, RATIO_PRECISION),
            'max_reimburse': max_reimburse,
            'remaining_quota': remaining_quota,
            'capped': capped,
            'segments': segments,
            'description': policy.get('description', ''),
            'formula': formula,
            'policy_year': self.bundle.year,
        }

    # ------------------------------------------------------------------
    def _reimburse_by_rate(self, above_deductible: float,
                           policy: Mapping[str, Any]):
        """按政策形态选择单一比例或分段比例。

        Returns:
            ``(segments, raw_reimburse, representative_rate)``
        """
        tiers = policy.get('reimburse_rates')
        if tiers:
            table = TierTable(tiers, name='大病保险报销比例')
            detail = table.breakdown(above_deductible)
            rate = detail['top_rate'] or table.max_rate
            return detail['segments'], detail['reimburse'], rate

        rate = float(policy.get('reimburse_rate', 0) or 0)
        return [], round_money(above_deductible * rate), rate

    def describe(self) -> Dict[str, Any]:
        """模块描述，附带当前政策参数。"""
        info = super().describe()
        info['policy'] = dict(self.policy)
        return info
