# -*- coding: utf-8 -*-
"""家庭账户共济（个人账户家庭共济）计算模块。

2021 年门诊共济改革后，职工医保个人账户余额可以授权给
**已参加基本医保**的配偶、父母、子女使用。

本模块能回答三个问题：

1. 我能不能把账户绑给这个人？（:meth:`validate_binding`）
2. 这笔钱能不能用共济账户付？（:meth:`can_pay`）
3. 家人自付 X 元，我的账户余额够不够、能付多少？（:meth:`calculate`）

核心约束：**共济的只有个人账户余额，统筹基金（报销部分）不可共济**。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..exceptions import InvalidInputError
from .base import PolicyModule, register_module, round_money

__all__ = ['HouseholdMutualAid']

#: 可共济成员的数量上限（政策默认值，可被 JSON 覆盖）
DEFAULT_BIND_LIMIT = 6


@register_module
class HouseholdMutualAid(PolicyModule):
    """个人账户家庭共济。"""

    code = 'household'
    name = '家庭共济'

    @property
    def policy(self) -> Dict[str, Any]:
        """家庭共济政策配置。"""
        return self.bundle.household

    def enabled(self) -> bool:
        """共济功能是否启用。"""
        return bool(self.policy.get('enabled'))

    @property
    def bind_limit(self) -> int:
        """最多可绑定几名家庭成员。"""
        return int(self.policy.get('bind_limit', DEFAULT_BIND_LIMIT))

    # ------------------------------------------------------------------
    def can_pay(self, usage: str) -> Dict[str, Any]:
        """判断某类用途能否使用共济账户支付。

        Args:
            usage: 用途代码，如 ``'member_outpatient_self_pay'``；
                也接受中文描述，会先做一次关键字匹配
        """
        allowed = list(self.policy.get('allowed_usage') or [])
        denied = list(self.policy.get('not_allowed_usage') or [])

        usage = (usage or '').strip()
        if usage in denied:
            return {'usage': usage, 'allowed': False, 'reason': '该用途明确不在共济范围内'}

        if usage in allowed:
            return {'usage': usage, 'allowed': True, 'reason': ''}

        # 中文模糊匹配：命中"报销/统筹"等关键词一律拒绝
        if any(word in usage for word in ('统筹', '报销', '基金支付', '非医疗', '非参保')):
            return {'usage': usage, 'allowed': False,
                    'reason': '统筹基金报销部分与非医疗消费不可共济'}

        return {
            'usage': usage,
            'allowed': False,
            'reason': '未知用途 %r，可共济用途：%s' % (usage, '、'.join(allowed)),
        }

    # ------------------------------------------------------------------
    def validate_binding(self, owner: Mapping[str, Any],
                         members: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        """校验一份家庭共济绑定申请。

        Args:
            owner: 授权人（账户主人）::

                {'name': '张三', 'insurance_type': 'employee', 'insured': True,
                 'account_balance': 8000}

            members: 被授权成员列表::

                [{'name': '李四', 'relation': 'spouse', 'insured': True}, ...]

        Returns:
            ``{'valid', 'errors', 'warnings', 'accepted', 'rejected'}``
        """
        errors: List[str] = []
        warnings: List[str] = []
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        relations = set(self.policy.get('member_relations') or [])
        owner_name = (owner or {}).get('name', '授权人')

        if not self.enabled():
            errors.append('当前政策版本未启用家庭共济')

        if not (owner or {}).get('insured', True):
            errors.append('%s 未参加基本医保，不能作为共济授权人' % owner_name)

        owner_insurance = (owner or {}).get('insurance_type', 'employee')
        if owner_insurance != 'employee':
            errors.append('%s 的险种为 %s，只有职工医保设有个人账户才能共济'
                          % (owner_name, owner_insurance))

        balance = float((owner or {}).get('account_balance', 0) or 0)
        if balance <= 0:
            warnings.append('%s 的个人账户余额为 0，绑定后暂时无法实际使用' % owner_name)

        members = list(members or [])
        if len(members) > self.bind_limit:
            errors.append('最多可绑定 %d 名家庭成员，当前提交 %d 名'
                          % (self.bind_limit, len(members)))

        seen = set()
        for member in members:
            name = (member or {}).get('name', '未命名成员')
            relation = (member or {}).get('relation', '')

            if not relation:
                rejected.append({'name': name, 'reason': '缺少亲属关系 relation'})
                continue
            if relation not in relations:
                rejected.append({'name': name, 'reason': '%s 不属于可共济亲属范围（%s）'
                                                       % (relation, '、'.join(sorted(relations)))})
                continue
            if name in seen:
                rejected.append({'name': name, 'reason': '重复提交'})
                continue
            if not (member or {}).get('insured', True):
                rejected.append({'name': name, 'reason': '被共济人须已参加基本医保'})
                continue

            seen.add(name)
            accepted.append({
                'name': name,
                'relation': relation,
                'insurance_type': (member or {}).get('insurance_type', ''),
            })

        return {
            'valid': not errors and not rejected,
            'owner': owner_name,
            'errors': errors,
            'warnings': warnings,
            'accepted': accepted,
            'rejected': rejected,
            'bind_limit': self.bind_limit,
            'allowed_relations': sorted(relations),
        }

    # ------------------------------------------------------------------
    def calculate(self, member_self_pay: float, account_balance: float, *,
                  usage: str = 'member_outpatient_self_pay',
                  member_name: str = '家庭成员') -> Dict[str, Any]:
        """计算共济账户能为某笔自付费用支付多少。

        Args:
            member_self_pay: 家庭成员本次需要自己承担的金额（**报销后**的自付部分）
            account_balance: 授权人个人账户可用余额
            usage: 用途代码
            member_name: 成员姓名，用于说明文案

        Returns:
            dict: 含 ``payable``（共济可支付）/ ``member_out_of_pocket``（仍需自付）
            / ``account_balance_after``（账户剩余）

        Raises:
            InvalidInputError: 金额为负
        """
        if member_self_pay < 0 or account_balance < 0:
            raise InvalidInputError('金额不能为负数')

        permission = self.can_pay(usage)
        member_self_pay = round_money(member_self_pay)
        account_balance = round_money(account_balance)

        if not self.enabled() or not permission['allowed']:
            payable = 0.0
            reason = permission['reason'] or '当前政策未启用家庭共济'
        else:
            payable = min(member_self_pay, account_balance)
            reason = '' if payable > 0 else '个人账户余额不足'

        return {
            'member_name': member_name,
            'usage': usage,
            'usage_allowed': permission['allowed'],
            'self_pay': member_self_pay,
            'account_balance': account_balance,
            'payable': round_money(payable),
            'member_out_of_pocket': round_money(member_self_pay - payable),
            'account_balance_after': round_money(account_balance - payable),
            'reason': reason,
            'formula': {
                'steps': [
                    {'key': 'payable', 'title': '共济账户支付',
                     'expression': 'min(自付 %s, 账户余额 %s) = %s 元'
                                   % (member_self_pay, account_balance, round_money(payable)),
                     'result': round_money(payable)},
                ],
                'text': '【家庭共济】%s 本次自付 %s 元，共济账户支付 %s 元，'
                        '账户剩余 %s 元，%s 还需自付 %s 元。'
                        % (member_name, member_self_pay, round_money(payable),
                           round_money(account_balance - payable), member_name,
                           round_money(member_self_pay - payable)),
            },
        }

    # ------------------------------------------------------------------
    def allocate(self, account_balance: float,
                 demands: Iterable[Mapping[str, Any]],
                 strategy: str = 'proportional') -> Dict[str, Any]:
        """余额不足时，在多个家庭成员之间分配共济额度。

        Args:
            account_balance: 可用余额
            demands: ``[{'name': '孩子', 'self_pay': 800, 'usage': '...'}, ...]``
            strategy: 分配策略

                * ``'proportional'``：按各人自付比例分摊（默认，最公平）
                * ``'priority'``：按列表顺序先到先得（用于"先给老人用"的场景）

        Returns:
            ``{'allocations', 'total_demand', 'total_payable', 'shortfall'}``
        """
        if strategy not in ('proportional', 'priority'):
            raise InvalidInputError('未知的分配策略 %r' % strategy)

        demand_list = []
        for entry in demands or []:
            name = entry.get('name', '未命名成员')
            usage = entry.get('usage', 'member_outpatient_self_pay')
            if entry.get('self_pay') is None:
                raise InvalidInputError('成员 %r 的需求缺少 self_pay 字段' % name)
            self_pay = round_money(float(entry['self_pay'] or 0))
            demand_list.append({
                'name': name,
                'usage': usage,
                'self_pay': self_pay,
                'allowed': self.can_pay(usage)['allowed'] and self.enabled(),
            })

        total_demand = round_money(sum(d['self_pay'] for d in demand_list))
        eligible = [d for d in demand_list if d['allowed'] and d['self_pay'] > 0]
        eligible_total = sum(d['self_pay'] for d in eligible)

        available = max(0.0, round_money(account_balance))
        allocations: List[Dict[str, Any]] = []

        # 两种策略共用一个"可分配池"：余额与总需求取小。
        # 比例分摊必须对**固定的池子**算份额——若按剩余余额逐人递减计算，
        # 后面的人拿到的是越来越小的份额，最后会剩下钱没分出去。
        pool = min(available, eligible_total)
        remaining = pool

        for demand in demand_list:
            payable = 0.0
            if demand['allowed'] and eligible_total > 0 and remaining > 0:
                if strategy == 'proportional':
                    share = demand['self_pay'] / eligible_total
                    payable = min(demand['self_pay'], round_money(pool * share), remaining)
                else:  # priority
                    payable = min(demand['self_pay'], remaining)
            payable = round_money(payable)
            allocations.append({
                'name': demand['name'],
                'usage': demand['usage'],
                'self_pay': demand['self_pay'],
                'allowed': demand['allowed'],
                'payable': payable,
                'out_of_pocket': round_money(demand['self_pay'] - payable),
            })
            remaining = round_money(remaining - payable)

        total_payable = round_money(sum(a['payable'] for a in allocations))
        return {
            'strategy': strategy,
            'account_balance': round_money(account_balance),
            'total_demand': total_demand,
            'total_payable': total_payable,
            'total_out_of_pocket': round_money(total_demand - total_payable),
            'shortfall': round_money(max(0.0, total_demand - total_payable)),
            'account_balance_after': round_money(max(0.0, account_balance) - total_payable),
            'allocations': allocations,
        }

    # ------------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """模块说明：能共济什么、给谁用、上限多少。"""
        info = super().describe()
        policy = self.policy
        info.update({
            'member_relations': list(policy.get('member_relations') or []),
            'bind_limit': self.bind_limit,
            'allowed_usage': list(policy.get('allowed_usage') or []),
            'not_allowed_usage': list(policy.get('not_allowed_usage') or []),
            'note': policy.get('note', ''),
        })
        return info
