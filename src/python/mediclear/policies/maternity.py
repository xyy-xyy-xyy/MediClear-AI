# -*- coding: utf-8 -*-
"""生育保险与生育津贴计算模块。

生育保险包含两块待遇：

1. **生育医疗费用**：产前检查、分娩、计划生育手术等，按"限额内实报实销"处理::

       某项目报销额 = min(实际花费, 该项目限额) × 报销比例

2. **生育津贴**（产假工资）::

       生育津贴 = 用人单位上年度职工月平均工资 ÷ 30 × 计发天数
       计发天数 = 法定天数（顺产 98 / 难产 113）+ 多胞胎每多一胎 +15 + 地方奖励假

注意：生育津贴由医保基金支付给单位，再由单位发放给个人；
津贴与本人工资"就高不就低"，差额由单位补足。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

from ..exceptions import InvalidInputError, PolicyNotFoundError
from .base import PolicyModule, register_module, round_money

__all__ = ['MaternityInsurance']

DAYS_PER_MONTH = 30
DAYS_PER_YEAR = 365

DELIVERY_TYPES = ('normal_delivery', 'difficult_delivery', 'cesarean', 'multiple_birth')

DELIVERY_LABELS = {
    'normal_delivery': '顺产',
    'difficult_delivery': '难产',
    'cesarean': '剖宫产',
    'multiple_birth': '多胞胎',
}


@register_module
class MaternityInsurance(PolicyModule):
    """生育保险待遇计算。"""

    code = 'maternity'
    name = '生育保险'

    @property
    def policy(self) -> Dict[str, Any]:
        """生育保险政策配置。"""
        return self.bundle.maternity

    def enabled(self) -> bool:
        """生育保险是否启用。"""
        return bool(self.policy.get('enabled'))

    # ------------------------------------------------------------------
    def items(self, city_code: str) -> Dict[str, Dict[str, Any]]:
        """获取某城市的生育医疗项目待遇（已应用城市限额覆盖）。"""
        if not self.bundle.has_city(city_code):
            raise PolicyNotFoundError('未收录城市 %r' % city_code, city=city_code)

        base_items = (self.policy.get('medical') or {}).get('items') or {}
        overrides = ((self.policy.get('medical') or {}).get('city_overrides') or {}) \
            .get(city_code) or {}

        resolved: Dict[str, Dict[str, Any]] = {}
        for code, item in base_items.items():
            merged = {
                'code': code,
                'name': item.get('name', code),
                'limit': float(item.get('limit', 0)),
                'rate': float(item.get('rate', 1.0)),
            }
            if code in overrides:
                merged.update({k: float(v) for k, v in overrides[code].items()
                               if k in ('limit', 'rate')})
                merged['is_city_override'] = True
            else:
                merged['is_city_override'] = False
            resolved[code] = merged
        return resolved

    def delivery_options(self, city_code: str) -> List[Dict[str, Any]]:
        """列出该城市可选的分娩方式及其津贴天数 / 医疗限额。"""
        items = self.items(city_code)
        options = []
        for code in DELIVERY_TYPES:
            item = items.get(code)
            options.append({
                'code': code,
                'name': DELIVERY_LABELS.get(code, code),
                'medical_limit': item['limit'] if item else 0.0,
                'allowance_days': self.allowance_days(city_code, code,
                                                      include_local_leave=False)['base_days'],
            })
        return options

    # ------------------------------------------------------------------
    def calculate_medical(self, city_code: str,
                          fees: Union[float, Mapping[str, float], Iterable[Mapping[str, Any]]],
                          delivery_type: Optional[str] = None) -> Dict[str, Any]:
        """计算生育医疗费用报销。

        Args:
            city_code: 城市代码
            fees: 三种写法之一
                * ``float``：单次分娩的总花费（需同时给 ``delivery_type``）
                * ``{'normal_delivery': 3000, 'prenatal_check': 1200}``：按项目代码
                * ``[{'type': 'normal_delivery', 'amount': 3000}, ...]``：明细列表
            delivery_type: 当 ``fees`` 为数字时，指定按哪类项目计算

        Returns:
            dict: 含 ``summary`` / ``items`` / ``formula``
        """
        items = self.items(city_code)
        normalized = self._normalize_fees(fees, delivery_type)

        details: List[Dict[str, Any]] = []
        total_fee = 0.0
        total_reimburse = 0.0

        for code, amount in normalized:
            item = items.get(code)
            amount = round_money(amount)
            total_fee = round_money(total_fee + amount)

            if item is None:
                details.append({
                    'code': code, 'name': code, 'amount': amount,
                    'limit': 0.0, 'rate': 0.0, 'reimburse': 0.0,
                    'self_pay': amount, 'note': '未知项目，按全额自付处理',
                    'is_city_override': False,
                })
                continue

            eligible = min(amount, item['limit'])
            reimburse = round_money(eligible * item['rate'])
            total_reimburse = round_money(total_reimburse + reimburse)
            details.append({
                'code': code,
                'name': item['name'],
                'amount': amount,
                'limit': round_money(item['limit']),
                'rate': item['rate'],
                'eligible': round_money(eligible),
                'reimburse': reimburse,
                'self_pay': round_money(amount - reimburse),
                'exceeds_limit': amount > item['limit'],
                'is_city_override': item.get('is_city_override', False),
                'formula': 'min(%s, 限额 %s) × %s%% = %s'
                           % (amount, round_money(item['limit']),
                              round(item['rate'] * 100, 1), reimburse),
            })

        personal_pay = round_money(total_fee - total_reimburse)

        return {
            'summary': {
                'total_fee': round_money(total_fee),
                'total_reimburse': total_reimburse,
                'personal_pay': personal_pay,
                'reimburse_ratio': round(total_reimburse / total_fee * 100, 1)
                                   if total_fee else 0.0,
            },
            'items': details,
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'formula': {
                'steps': [
                    {'key': 'reimburse', 'title': '生育医疗费报销',
                     'expression': 'Σ min(实际花费, 项目限额) × 比例 = %s 元' % total_reimburse,
                     'result': total_reimburse},
                ],
                'text': '【生育医疗费用】限额内按比例报销，本次报销 %s 元，'
                        '自付 %s 元（超出限额部分需自付）。' % (total_reimburse, personal_pay),
            },
            'policy_year': self.bundle.year,
        }

    # ------------------------------------------------------------------
    def allowance_days(self, city_code: str, delivery_type: str = 'normal_delivery',
                       babies: int = 1, include_local_leave: bool = True) -> Dict[str, Any]:
        """计算生育津贴计发天数。

        Args:
            city_code: 城市代码（用于地方奖励假）
            delivery_type: 分娩方式
            babies: 婴儿数量，多胞胎每多一胎加 ``extra_per_extra_baby`` 天
            include_local_leave: 是否计入地方奖励假
        """
        allowance = self.policy.get('allowance') or {}
        paid_days = allowance.get('paid_days') or {}

        # 剖宫产在政策口径里按"难产"计发（多 15 天）；医疗费项目另有 cesarean 一档。
        paid_key = 'difficult_delivery' if delivery_type == 'cesarean' else delivery_type
        if paid_key not in paid_days:
            raise InvalidInputError(
                '未知的分娩方式 %r，可选：%s' % (delivery_type, sorted(DELIVERY_TYPES)))

        base_days = int(paid_days[paid_key])
        babies = max(1, int(babies or 1))
        extra_per_baby = int(paid_days.get('extra_per_extra_baby', 0))
        multiple_extra = extra_per_baby * (babies - 1)

        local_extra = 0
        if include_local_leave:
            local_extra = int((allowance.get('local_leave_extra_days') or {})
                              .get(city_code, 0))

        min_days = int(allowance.get('min_days', 98))
        total_days = max(min_days, base_days + multiple_extra + local_extra)

        return {
            'delivery_type': delivery_type,
            'delivery_name': DELIVERY_LABELS.get(delivery_type, delivery_type),
            'base_days': base_days,
            'babies': babies,
            'multiple_extra_days': multiple_extra,
            'local_extra_days': local_extra,
            'include_local_leave': include_local_leave,
            'total_days': total_days,
            'min_days': min_days,
        }

    def calculate_allowance(self, city_code: str, monthly_avg_wage: float, *,
                            delivery_type: str = 'normal_delivery',
                            babies: int = 1,
                            include_local_leave: bool = True,
                            personal_monthly_wage: Optional[float] = None) -> Dict[str, Any]:
        """计算生育津贴。

        Args:
            city_code: 城市代码
            monthly_avg_wage: 用人单位上年度职工月平均工资（元/月）
            delivery_type: 分娩方式
            babies: 婴儿数量
            include_local_leave: 是否把地方奖励假计入津贴天数
            personal_monthly_wage: 本人产假前工资，用于计算"单位补差"

        Returns:
            dict: 含 ``allowance`` / ``days`` / ``daily_wage`` / ``makeup`` / ``formula``

        Raises:
            InvalidInputError: 月平均工资非法
        """
        if not self.bundle.has_city(city_code):
            raise PolicyNotFoundError('未收录城市 %r' % city_code, city=city_code)
        wage = float(monthly_avg_wage)
        if wage <= 0:
            raise InvalidInputError('用人单位月平均工资必须为正数，收到 %r' % monthly_avg_wage)

        days_info = self.allowance_days(city_code, delivery_type, babies, include_local_leave)
        daily_wage = wage / DAYS_PER_MONTH
        allowance = round_money(daily_wage * days_info['total_days'])

        # 津贴低于本人工资时，差额由单位补足
        makeup = 0.0
        if personal_monthly_wage is not None:
            personal_monthly_wage = float(personal_monthly_wage)
            personal_total = personal_monthly_wage / DAYS_PER_MONTH * days_info['total_days']
            makeup = round_money(max(0.0, personal_total - allowance))

        formula_text = (
            '【生育津贴】\n'
            '单位上年度月平均工资 %s 元 ÷ 30 × 计发天数 %s 天 = %s 元\n'
            '计发天数 = 法定 %s 天%s%s'
        ) % (round_money(wage), days_info['total_days'], allowance, days_info['base_days'],
             (' + 多胞胎 %d 天' % days_info['multiple_extra_days'])
             if days_info['multiple_extra_days'] else '',
             (' + 地方奖励假 %d 天' % days_info['local_extra_days'])
             if days_info['local_extra_days'] else '')

        return {
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'monthly_avg_wage': round_money(wage),
            'daily_wage': round_money(daily_wage),
            'days': days_info,
            'allowance': allowance,
            'personal_monthly_wage': personal_monthly_wage,
            'makeup_by_employer': makeup,
            'total_benefit': round_money(allowance + makeup),
            'formula': {
                'steps': [
                    {'key': 'daily_wage', 'title': '日工资',
                     'expression': '%s ÷ 30 = %s 元' % (round_money(wage), round_money(daily_wage)),
                     'result': round_money(daily_wage)},
                    {'key': 'days', 'title': '计发天数',
                     'expression': '%s 天' % days_info['total_days'], 'result': days_info['total_days']},
                    {'key': 'allowance', 'title': '生育津贴',
                     'expression': '%s × %s = %s 元'
                                   % (round_money(daily_wage), days_info['total_days'], allowance),
                     'result': allowance},
                ],
                'text': formula_text,
            },
            'note': '生育津贴由医保基金支付给用人单位，再由单位发放给个人；'
                    '津贴低于本人工资的差额由单位补足。',
        }

    def calculate(self, city_code: str, *, fees=None, monthly_avg_wage=None,
                  delivery_type: str = 'normal_delivery', babies: int = 1,
                  include_local_leave: bool = True,
                  personal_monthly_wage: Optional[float] = None) -> Dict[str, Any]:
        """一次性计算生育医疗费用 + 生育津贴。

        两个部分都可单独省略：只传 ``fees`` 就只算医疗费，只传 ``monthly_avg_wage``
        就只算津贴。
        """
        result: Dict[str, Any] = {
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'delivery_type': delivery_type,
            'delivery_name': DELIVERY_LABELS.get(delivery_type, delivery_type),
            'policy_year': self.bundle.year,
        }

        if fees is not None:
            result['medical'] = self.calculate_medical(city_code, fees, delivery_type)
        if monthly_avg_wage is not None:
            result['allowance'] = self.calculate_allowance(
                city_code, monthly_avg_wage,
                delivery_type=delivery_type, babies=babies,
                include_local_leave=include_local_leave,
                personal_monthly_wage=personal_monthly_wage)

        medical_total = (result.get('medical') or {}).get('summary', {}).get('total_reimburse', 0.0)
        allowance_total = (result.get('allowance') or {}).get('allowance', 0.0)
        result['total_benefit'] = round_money(medical_total + allowance_total)
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_fees(fees, delivery_type: Optional[str]):
        """把三种 ``fees`` 写法统一成 ``[(项目代码, 金额), ...]``。"""
        if isinstance(fees, (int, float)) and not isinstance(fees, bool):
            if not delivery_type:
                raise InvalidInputError('以数字形式给费用时必须指定 delivery_type')
            return [(delivery_type, float(fees))]

        if isinstance(fees, Mapping):
            return [(str(k), float(v)) for k, v in fees.items()]

        if isinstance(fees, Iterable):
            result = []
            for entry in fees:
                if not isinstance(entry, Mapping):
                    raise InvalidInputError('费用明细必须是对象，收到 %r' % (entry,))
                code = entry.get('type') or entry.get('code') or entry.get('item')
                if not code:
                    raise InvalidInputError('费用明细缺少 type/code 字段：%r' % (entry,))
                result.append((str(code), float(entry.get('amount', 0))))
            return result

        raise InvalidInputError('无法识别的费用入参：%r' % (fees,))

    def describe(self) -> Dict[str, Any]:  # pragma: no cover - 展示用
        info = super().describe()
        info['delivery_types'] = list(DELIVERY_TYPES)
        return info
