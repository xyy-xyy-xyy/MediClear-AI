# -*- coding: utf-8 -*-
"""医保缴费计算模块（每月交多少钱 / 单位与个人各承担多少）。

三种人群::

    在职职工：月缴费 = 缴费基数 × (个人费率 + 单位费率 + 生育费率)
              个人只承担 2%，其余由单位承担
    灵活就业：月缴费 = 缴费基数 × 费率（自选是否建立个人账户）
    城乡居民：按年缴费，个人缴费 + 财政补助，无缴费基数概念
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..constants import EMPLOYEE, RESIDENT
from ..exceptions import InsufficientDataError, InvalidInputError, PolicyNotFoundError
from .base import PolicyModule, register_module, round_money

__all__ = ['ContributionPolicy']

MONTHS_PER_YEAR = 12

EMPLOYMENT_EMPLOYED = 'employed'
EMPLOYMENT_FLEXIBLE = 'flexible'


@register_module
class ContributionPolicy(PolicyModule):
    """医保缴费测算。"""

    code = 'contribution'
    name = '医保缴费'

    @property
    def policy(self) -> Dict[str, Any]:
        """缴费政策配置。"""
        return self.bundle.contribution

    # ------------------------------------------------------------------
    def monthly(self, city_code: str, insurance_type: str = EMPLOYEE, *,
                base_salary: Optional[float] = None,
                employment: str = EMPLOYMENT_EMPLOYED,
                with_account: bool = True) -> Dict[str, Any]:
        """计算每月缴费。

        Args:
            city_code: 城市代码
            insurance_type: 险种（``employee`` / ``resident``）
            base_salary: 缴费基数，缺省按当地下限
            employment: ``'employed'``（在职）或 ``'flexible'``（灵活就业）
            with_account: 灵活就业人员是否建立个人账户

        Returns:
            dict: 含 ``personal_monthly`` / ``employer_monthly`` / ``total_monthly`` /
            ``personal_annual`` / ``total_annual`` / ``formula``

        Raises:
            PolicyNotFoundError: 城市未收录
            InvalidInputError: 险种或就业形态非法
            InsufficientDataError: 在职职工未提供缴费基数
        """
        if not self.bundle.has_city(city_code):
            raise PolicyNotFoundError('未收录城市 %r' % city_code, city=city_code)
        if insurance_type not in (EMPLOYEE, RESIDENT):
            raise InvalidInputError('未知险种 %r' % insurance_type)
        if employment not in (EMPLOYMENT_EMPLOYED, EMPLOYMENT_FLEXIBLE):
            raise InvalidInputError('未知就业形态 %r' % employment)

        if insurance_type == RESIDENT:
            return self._resident(city_code, employment)

        if employment == EMPLOYMENT_FLEXIBLE:
            return self._flexible(city_code, base_salary, with_account)

        return self._employee(city_code, base_salary)

    def annual(self, city_code: str, insurance_type: str = EMPLOYEE, **kwargs: Any) -> Dict[str, Any]:
        """计算年度缴费（月缴费 × 12；居民医保本身即为年缴）。"""
        result = self.monthly(city_code, insurance_type, **kwargs)
        result['personal_annual'] = round_money(result.get('personal_monthly', 0) * MONTHS_PER_YEAR) \
            if insurance_type == EMPLOYEE else result.get('personal_annual', 0)
        result['total_annual'] = round_money(result.get('total_monthly', 0) * MONTHS_PER_YEAR) \
            if insurance_type == EMPLOYEE else result.get('total_annual', 0)
        result['employer_annual'] = round_money(result.get('employer_monthly', 0) * MONTHS_PER_YEAR) \
            if insurance_type == EMPLOYEE else 0.0
        return result

    def compare_cities(self, city_codes, insurance_type: str = EMPLOYEE,
                       base_salary: Optional[float] = None,
                       **kwargs: Any) -> Dict[str, Any]:
        """横向对比多个城市的缴费水平。

        Args:
            city_codes: 城市代码序列
        """
        rows = []
        for city_code in city_codes:
            try:
                row = self.monthly(city_code, insurance_type,
                                   base_salary=base_salary, **kwargs)
            except (PolicyNotFoundError, InvalidInputError, InsufficientDataError) as exc:
                rows.append({'city_code': city_code, 'error': str(exc)})
                continue
            rows.append({
                'city_code': city_code,
                'city_name': row.get('city_name', city_code),
                'base': row.get('base'),
                'personal_monthly': row['personal_monthly'],
                'personal_annual': round_money(row['personal_monthly'] * MONTHS_PER_YEAR),
                'total_monthly': row['total_monthly'],
                'total_annual': round_money(row['total_monthly'] * MONTHS_PER_YEAR),
            })
        rows.sort(key=lambda r: r.get('personal_monthly', 0))
        return {'insurance_type': insurance_type, 'rows': rows}

    # ------------------------------------------------------------------
    def _clamp_base(self, city_code: str, base_salary: Optional[float]) -> Dict[str, Any]:
        """缴费基数上下限约束。"""
        limits = self.bundle.contribution_base_limits(city_code)
        if base_salary is None:
            return {
                'base': round_money(limits['min']),
                'raw': None,
                'min': round_money(limits['min']),
                'max': round_money(limits['max']),
                'clamped': False,
                'clamp_reason': '未提供缴费基数，按当地下限 %s 元估算' % round_money(limits['min']),
                'is_default_limit': limits.get('is_default', False),
            }
        if base_salary <= 0:
            raise InvalidInputError('缴费基数必须是正数，收到 %r' % base_salary)

        low, high = limits['min'], limits['max']
        if base_salary < low:
            base, reason = low, '缴费基数低于下限，按下限 %s 元计算' % round_money(low)
        elif base_salary > high:
            base, reason = high, '缴费基数高于上限，按上限 %s 元计算' % round_money(high)
        else:
            base, reason = float(base_salary), ''
        return {
            'base': round_money(base),
            'raw': round_money(base_salary),
            'min': round_money(low),
            'max': round_money(high),
            'clamped': bool(reason),
            'clamp_reason': reason,
            'is_default_limit': limits.get('is_default', False),
        }

    def _employee(self, city_code: str, base_salary: Optional[float]) -> Dict[str, Any]:
        """在职职工缴费。"""
        block = self.policy.get('employee') or {}
        personal_rate = float(block.get('personal_rate', 0.02))
        employer_rate = float(block.get('employer_rate', 0.08))
        maternity_rate = float(block.get('maternity_rate', 0.0))
        merged = bool(block.get('maternity_merged', True))

        clamped = self._clamp_base(city_code, base_salary)
        base = clamped['base']

        personal = round_money(base * personal_rate)
        employer = round_money(base * employer_rate)
        maternity = round_money(base * maternity_rate) if merged else 0.0
        total = round_money(personal + employer + maternity)

        steps = [
            {'key': 'base', 'title': '缴费基数', 'expression': '%s 元/月' % base,
             'result': base, 'note': clamped['clamp_reason']},
            {'key': 'personal', 'title': '个人缴纳',
             'expression': '%s × %s%% = %s 元/月'
                           % (base, round(personal_rate * 100, 2), personal),
             'result': personal},
            {'key': 'employer', 'title': '单位缴纳',
             'expression': '%s × %s%% = %s 元/月'
                           % (base, round(employer_rate * 100, 2), employer),
             'result': employer},
        ]
        if maternity > 0:
            steps.append({
                'key': 'maternity', 'title': '生育保险（单位缴纳）',
                'expression': '%s × %s%% = %s 元/月'
                              % (base, round(maternity_rate * 100, 2), maternity),
                'result': maternity,
            })
        steps.append({'key': 'total', 'title': '每月合计',
                      'expression': '%s 元/月' % total, 'result': total})

        return {
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'insurance_type': EMPLOYEE,
            'employment': EMPLOYMENT_EMPLOYED,
            'base': base,
            'base_limits': {'min': clamped['min'], 'max': clamped['max']},
            'base_clamped': clamped['clamped'],
            'base_clamp_reason': clamped['clamp_reason'],
            'personal_rate': personal_rate,
            'employer_rate': employer_rate,
            'maternity_rate': maternity_rate,
            'personal_monthly': personal,
            'employer_monthly': round_money(employer + maternity),
            'employer_medical_monthly': employer,
            'maternity_monthly': maternity,
            'total_monthly': total,
            'personal_annual': round_money(personal * MONTHS_PER_YEAR),
            'employer_annual': round_money((employer + maternity) * MONTHS_PER_YEAR),
            'total_annual': round_money(total * MONTHS_PER_YEAR),
            'employer_share': round((employer + maternity) / total * 100, 1) if total else 0.0,
            'formula': {
                'steps': steps,
                'text': (
                    '【职工医保缴费】\n'
                    '缴费基数 %s 元/月\n'
                    '个人：%s%% → %s 元/月（全年 %s 元）\n'
                    '单位：%s%% + 生育 %s%% → %s 元/月\n'
                    '合计 %s 元/月，全年 %s 元'
                ) % (base, round(personal_rate * 100, 2), personal,
                     round_money(personal * MONTHS_PER_YEAR),
                     round(employer_rate * 100, 2), round(maternity_rate * 100, 2),
                     round_money(employer + maternity), total,
                     round_money(total * MONTHS_PER_YEAR)),
            },
        }

    def _flexible(self, city_code: str, base_salary: Optional[float],
                  with_account: bool) -> Dict[str, Any]:
        """灵活就业人员缴费。

        灵活就业人员**自己承担全部费用**，且可以在两种档位中选择：

        * 不建个人账户：只按统筹比例缴费，费率较低（``rate``）；
        * 建立个人账户：在统筹比例之外**再缴** ``account_rate``，
          这部分全额划入个人账户。

        所以建账户的人每月交得更多，但医保卡里也会有钱——这正是两者的取舍。
        """
        block = self.policy.get('flexible_employment') or {}
        base_rate = float(block.get('rate', 0.08))
        account_rate = float(block.get('account_rate', 0.02)) if with_account else 0.0
        rate = base_rate + account_rate

        clamped = self._clamp_base(city_code, base_salary)
        base = clamped['base']
        monthly = round_money(base * rate)
        account_part = round_money(base * account_rate)

        return {
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'insurance_type': EMPLOYEE,
            'employment': EMPLOYMENT_FLEXIBLE,
            'base': base,
            'base_limits': {'min': clamped['min'], 'max': clamped['max']},
            'base_clamped': clamped['clamped'],
            'base_clamp_reason': clamped['clamp_reason'],
            'rate': rate,
            'base_rate': base_rate,
            'with_account': with_account,
            'account_rate': account_rate,
            'personal_monthly': monthly,
            'employer_monthly': 0.0,
            'total_monthly': monthly,
            'account_monthly': account_part,
            'personal_annual': round_money(monthly * MONTHS_PER_YEAR),
            'total_annual': round_money(monthly * MONTHS_PER_YEAR),
            'formula': {
                'steps': [
                    {'key': 'base', 'title': '缴费基数', 'expression': '%s 元/月' % base,
                     'result': base, 'note': clamped['clamp_reason']},
                    {'key': 'rate', 'title': '缴费比例',
                     'expression': '%s%%（统筹 %s%%%s）'
                                   % (round(rate * 100, 2), round(base_rate * 100, 2),
                                      ' + 个人账户 %s%%' % round(account_rate * 100, 2)
                                      if with_account else '，不建个人账户'),
                     'result': round(rate, 4)},
                    {'key': 'total', 'title': '灵活就业缴费',
                     'expression': '%s × %s%% = %s 元/月'
                                   % (base, round(rate * 100, 2), monthly),
                     'result': monthly},
                ],
                'text': '【灵活就业缴费】按缴费基数 %s 元的 %s%% 缴纳，每月 %s 元，全年 %s 元。%s'
                        % (base, round(rate * 100, 2), monthly,
                           round_money(monthly * MONTHS_PER_YEAR),
                           ('其中 %s 元/月划入个人账户。' % account_part) if with_account
                           else '未建立个人账户，费用全部进入统筹基金。'),
            },
        }

    def _resident(self, city_code: str, employment: str) -> Dict[str, Any]:
        """城乡居民医保按年缴费，无缴费基数。"""
        block = self.policy.get('resident') or {}
        personal = float(block.get('adult_personal', 0))
        subsidy = float(block.get('adult_subsidy', 0))
        total = round_money(personal + subsidy)

        return {
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'insurance_type': RESIDENT,
            'employment': employment,
            'mode': block.get('mode', 'annual'),
            'base': None,
            'personal_monthly': round_money(personal / MONTHS_PER_YEAR),
            'employer_monthly': 0.0,
            'total_monthly': round_money(total / MONTHS_PER_YEAR),
            'personal_annual': round_money(personal),
            'government_subsidy_annual': round_money(subsidy),
            'total_annual': total,
            'insured_share': round(personal / total * 100, 1) if total else 0.0,
            'formula': {
                'steps': [
                    {'key': 'personal_annual', 'title': '个人缴费（年）',
                     'expression': '%s 元/年' % personal, 'result': round_money(personal)},
                    {'key': 'subsidy', 'title': '财政补助（年）',
                     'expression': '%s 元/年' % subsidy, 'result': round_money(subsidy)},
                    {'key': 'total', 'title': '筹资合计',
                     'expression': '%s + %s = %s 元/年'
                                   % (personal, subsidy, total), 'result': total},
                ],
                'text': ('【居民医保缴费】按年缴费，个人缴纳 %s 元，财政补助 %s 元，'
                         '合计筹资 %s 元/年。') % (personal, subsidy, total),
            },
        }

    def calculate(self, city_code: str, insurance_type: str = EMPLOYEE,
                  **kwargs: Any) -> Dict[str, Any]:
        """统一入口，等价于 :meth:`monthly`（便于按模块代码统一调用）。"""
        return self.monthly(city_code, insurance_type, **kwargs)

    def describe(self) -> Dict[str, Any]:  # pragma: no cover - 展示用
        info = super().describe()
        info['policy'] = dict(self.policy)
        return info
