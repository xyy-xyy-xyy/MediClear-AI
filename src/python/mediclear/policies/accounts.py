# -*- coding: utf-8 -*-
"""职工医保个人账户计入规则。

"每月医保卡里返多少钱"是这个模块回答的问题。

计入规则（门诊共济改革后的主流口径）::

    在职人员：月计入 = 缴费基数 × (个人缴费比例 + 单位划入比例)
              个人缴费比例一般为 2%，单位缴费原则上不再划入个人账户
    退休人员：月计入 = 城市定额（与养老金脱钩）
    居民医保：不设个人账户

缴费基数受当地上下限约束（低于下限按下限、高于上限按上限）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from ..constants import EMPLOYEE, RESIDENT
from ..exceptions import InsufficientDataError, PolicyNotFoundError
from .base import PolicyModule, register_module, round_money

__all__ = ['PersonalAccountPolicy']

MONTHS_PER_YEAR = 12


@register_module
class PersonalAccountPolicy(PolicyModule):
    """个人账户（医保卡个人账户）计入与结余测算。"""

    code = 'personal_account'
    name = '个人账户'

    @property
    def policy(self) -> Dict[str, Any]:
        """个人账户政策配置。"""
        return self.bundle.personal_account

    def enabled(self) -> bool:
        """本政策版本是否启用个人账户。"""
        return bool(self.policy.get('enabled'))

    # ------------------------------------------------------------------
    def supported(self, insurance_type: str) -> bool:
        """该险种是否有个人账户（居民医保没有）。"""
        if not self.enabled():
            return False
        if insurance_type == RESIDENT:
            return bool((self.policy.get('resident') or {}).get('enabled'))
        return insurance_type == EMPLOYEE

    # ------------------------------------------------------------------
    def clamp_base(self, city_code: str, base_salary: float) -> Dict[str, Any]:
        """把缴费基数限制在当地的上下限区间内。

        Returns:
            ``{'base', 'raw', 'min', 'max', 'clamped', 'clamp_reason', 'is_default_limit'}``
        """
        limits = self.bundle.contribution_base_limits(city_code)
        raw = float(base_salary if base_salary is not None else limits['min'])
        low, high = limits['min'], limits['max']

        if raw < low:
            base, reason = low, '缴费基数低于当地下限，按下限 %s 元计算' % round_money(low)
        elif raw > high:
            base, reason = high, '缴费基数高于当地上限，按上限 %s 元计算' % round_money(high)
        else:
            base, reason = raw, ''

        return {
            'base': round_money(base),
            'raw': round_money(raw),
            'min': round_money(low),
            'max': round_money(high),
            'clamped': bool(reason),
            'clamp_reason': reason,
            'is_default_limit': limits.get('is_default', False),
        }

    # ------------------------------------------------------------------
    def monthly_credit(self, city_code: str, insurance_type: str = EMPLOYEE, *,
                       base_salary: Optional[float] = None,
                       retired: bool = False) -> Dict[str, Any]:
        """计算每月计入个人账户的金额。

        Args:
            city_code: 城市代码
            insurance_type: 险种
            base_salary: 缴费基数（在职人员必填；缺省按当地下限）
            retired: 是否退休人员（退休按城市定额划入）

        Returns:
            dict: 含 ``monthly_credit`` / ``personal_part`` / ``employer_part`` /
            ``formula`` 等

        Raises:
            InsufficientDataError: 未提供缴费基数且无法推断
        """
        if not self.bundle.has_city(city_code):
            raise PolicyNotFoundError('未收录城市 %r' % city_code, city=city_code)

        employee_policy = self.policy.get('employee') or {}

        if not self.supported(insurance_type):
            return {
                'city_code': city_code,
                'insurance_type': insurance_type,
                'supported': False,
                'monthly_credit': 0.0,
                'annual_credit': 0.0,
                'message': '居民医保不设个人账户，普通门诊由统筹基金报销',
                'formula': {'steps': [], 'text': '居民医保无个人账户。'},
            }

        if retired:
            return self._retired_credit(city_code, insurance_type, employee_policy)

        if base_salary is None:
            raise InsufficientDataError('计算在职人员个人账户需提供缴费基数 base_salary')

        active = employee_policy.get('active') or {}
        personal_rate = float(active.get('personal_rate', 0.02))
        employer_rate_default = float(active.get('employer_rate_to_account', 0.0))
        employer_rate = float(
            (active.get('employer_rate_to_account_by_city') or {})
            .get(city_code, employer_rate_default))

        clamped = self.clamp_base(city_code, base_salary)
        base = clamped['base']

        personal_part = round_money(base * personal_rate)
        employer_part = round_money(base * employer_rate)
        monthly = round_money(personal_part + employer_part)

        steps = [
            {'key': 'base', 'title': '缴费基数',
             'expression': '%s 元' % base, 'result': base,
             'note': clamped['clamp_reason']},
            {'key': 'personal_part', 'title': '个人缴费划入',
             'expression': '%s × %s%% = %s 元'
                           % (base, round(personal_rate * 100, 2), personal_part),
             'result': personal_part},
        ]
        if employer_rate > 0:
            steps.append({
                'key': 'employer_part', 'title': '单位缴费划入',
                'expression': '%s × %s%% = %s 元'
                              % (base, round(employer_rate * 100, 2), employer_part),
                'result': employer_part,
            })
        else:
            steps.append({
                'key': 'employer_part', 'title': '单位缴费划入',
                'expression': '0 元（门诊共济改革后单位缴费不再划入个人账户）',
                'result': 0.0,
            })

        steps.append({
            'key': 'monthly_credit', 'title': '每月计入合计',
            'expression': '%s + %s = %s 元' % (personal_part, employer_part, monthly),
            'result': monthly,
        })

        return {
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'insurance_type': insurance_type,
            'supported': True,
            'retired': False,
            'personal_rate': personal_rate,
            'employer_rate': employer_rate,
            'base': base,
            'base_limits': {'min': clamped['min'], 'max': clamped['max']},
            'base_clamped': clamped['clamped'],
            'base_clamp_reason': clamped['clamp_reason'],
            'personal_part': personal_part,
            'employer_part': employer_part,
            'monthly_credit': monthly,
            'annual_credit': round_money(monthly * MONTHS_PER_YEAR),
            'formula': {
                'steps': steps,
                'text': (
                    '【个人账户计入】\n'
                    '缴费基数 %s 元\n'
                    '个人缴费按 %s%% 划入：%s 元\n'
                    '单位缴费划入：%s 元（比例 %s%%）\n'
                    '每月合计 %s 元，全年约 %s 元'
                ) % (base, round(personal_rate * 100, 2), personal_part,
                     employer_part, round(employer_rate * 100, 2),
                     monthly, round_money(monthly * MONTHS_PER_YEAR)),
            },
        }

    # ------------------------------------------------------------------
    def _retired_credit(self, city_code: str, insurance_type: str,
                        employee_policy: Dict[str, Any]) -> Dict[str, Any]:
        """退休人员定额划入。"""
        retired_policy = employee_policy.get('retired') or {}
        by_city = retired_policy.get('by_city') or {}
        amount = float(by_city.get(city_code, retired_policy.get('monthly_amount', 0)) or 0)
        is_override = city_code in by_city

        return {
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'insurance_type': insurance_type,
            'supported': True,
            'retired': True,
            'mode': retired_policy.get('mode', 'flat'),
            'is_city_override': is_override,
            'personal_part': amount,
            'employer_part': 0.0,
            'monthly_credit': round_money(amount),
            'annual_credit': round_money(amount * MONTHS_PER_YEAR),
            'base': None,
            'formula': {
                'steps': [{
                    'key': 'monthly_credit',
                    'title': '退休人员每月计入',
                    'expression': '按 %s 定额划入 = %s 元'
                                  % (self.bundle.city(city_code).get('name', city_code), amount),
                    'result': round_money(amount),
                }],
                'text': '【个人账户计入】退休人员按当地定额划入，每月 %s 元，全年约 %s 元。'
                        % (amount, round_money(amount * MONTHS_PER_YEAR)),
            },
        }

    # ------------------------------------------------------------------
    def project(self, city_code: str, insurance_type: str = EMPLOYEE, *,
                base_salary: Optional[float] = None, months: int = MONTHS_PER_YEAR,
                retired: bool = False, opening_balance: float = 0.0,
                monthly_spend: float = 0.0) -> Dict[str, Any]:
        """测算一段时间后的账户余额（考虑每月消费）。

        Args:
            months: 测算月数，默认 12
            opening_balance: 期初余额
            monthly_spend: 每月从个人账户支出的金额（买药、门诊自付等）
        """
        credit = self.monthly_credit(
            city_code, insurance_type, base_salary=base_salary, retired=retired)
        monthly_credit = credit['monthly_credit']

        balance = float(opening_balance or 0.0)
        months = max(0, int(months))
        timeline = []
        for month in range(1, months + 1):
            inflow = monthly_credit
            outflow = min(float(monthly_spend or 0.0), balance + inflow)
            balance = round_money(balance + inflow - outflow)
            timeline.append({
                'month': month,
                'inflow': inflow,
                'outflow': round_money(outflow),
                'balance': balance,
            })

        return {
            'monthly_credit': monthly_credit,
            'months': months,
            'opening_balance': round_money(opening_balance),
            'total_inflow': round_money(monthly_credit * months),
            'total_outflow': round_money(sum(t['outflow'] for t in timeline)),
            'closing_balance': round_money(balance),
            'insufficient_month': next(
                (t['month'] for t in timeline if t['balance'] <= 0), None),
            'timeline': timeline,
            'detail': credit,
        }

    def calculate(self, city_code: str, insurance_type: str = EMPLOYEE,
                  **kwargs: Any) -> Dict[str, Any]:
        """统一入口，等价于 :meth:`project`（便于按模块代码统一调用）。"""
        return self.project(city_code, insurance_type, **kwargs)

    def describe(self) -> Dict[str, Any]:  # pragma: no cover - 展示用
        info = super().describe()
        info['employee'] = self.policy.get('employee', {})
        return info
