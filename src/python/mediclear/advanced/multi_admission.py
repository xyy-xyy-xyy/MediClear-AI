# -*- coding: utf-8 -*-
"""多次住院累计计算（起付线递减 + 年度累计封顶 + 大病累计）。

与单次计算的区别：

* **起付线递减**：同一年度内第 1 次住院付全额起付线，第 2 次减半，
  第 3 次起免起付线（系数来自 ``extended.json`` 的 ``multi_admission``，各城市不同）
* **封顶线累计**：每次报销都占用统筹基金的年度额度，用完即止
* **大病保险累计**：大病保险按"年度累计个人自付"计算，
  所以第 N 次的大病报销 = f(累计自付_N) − f(累计自付_{N-1})，而不是每次单独算

示例::

    sim = MultiAdmissionCalculator(bundle)
    result = sim.simulate(
        [{'amount': 30000}, {'amount': 20000}, {'amount': 15000}],
        city_code='beijing', insurance_type='employee',
    )
    result['state']['deductible_paid']   # 全年实际支付的起付线合计
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..constants import EMPLOYEE, INPATIENT, OUTPATIENT
from ..data.repository import PolicyBundle
from ..exceptions import InvalidInputError, PolicyNotFoundError
from ..policies.base import TierTable, resolve_tiers, round_money
from ..policies.serious_illness import SeriousIllnessInsurance
from .year_state import VisitRecord, YearState

__all__ = ['MultiAdmissionCalculator']

DEFAULT_CLASS_RATIOS = {'class_a': 0.6, 'class_b': 0.3, 'self_pay': 0.1}


class MultiAdmissionCalculator:
    """一年内多次就医的累计报销模拟器。

    Args:
        bundle: 政策数据包
    """

    def __init__(self, bundle: PolicyBundle, decrease_deductible: bool = True):
        """
        Args:
            bundle: 政策数据包
            decrease_deductible: 是否应用起付线递减规则。
                设为 ``False`` 可用于对照实验（"假设每次都付全额起付线"）。
        """
        self.bundle = bundle
        self._serious = SeriousIllnessInsurance(bundle)
        self.decrease_deductible = decrease_deductible

    # ------------------------------------------------------------------
    @property
    def policy(self) -> Dict[str, Any]:
        """多次住院政策配置。"""
        return self.bundle.multi_admission

    def enabled(self) -> bool:
        """起付线递减规则是否生效（政策启用 **且** 本次模拟未关闭）。"""
        return bool(self.policy.get('enabled')) and self.decrease_deductible

    def deductible_factors(self, city_code: str) -> List[float]:
        """获取该城市的起付线递减系数序列。"""
        override = (self.policy.get('city_overrides') or {}).get(city_code)
        bucket = override or (self.policy.get('default') or {})
        factors = bucket.get('deductible_factors') or [1.0]
        return [float(f) for f in factors]

    def deductible_for(self, city_code: str, admission_index: int,
                       base_deductible: float) -> Dict[str, Any]:
        """计算第 ``admission_index`` 次（从 1 开始）住院应承担的起付线。

        Args:
            city_code: 城市代码
            admission_index: 住院次序（1 = 第一次）
            base_deductible: 政策的基准起付线

        Returns:
            ``{'deductible', 'factor', 'base_deductible', 'admission_index', 'note'}``
        """
        if admission_index < 1:
            raise InvalidInputError('住院次序必须从 1 开始，收到 %r' % admission_index)

        factors = self.deductible_factors(city_code)
        index = min(admission_index, len(factors)) - 1
        factor = factors[index] if self.enabled() else 1.0
        deductible = round_money(float(base_deductible) * factor)

        if factor >= 1.0:
            note = '第 %d 次住院，起付线全额 %s 元' % (admission_index, round_money(base_deductible))
        elif factor <= 0.0:
            note = '第 %d 次住院，按政策免起付线' % admission_index
        else:
            note = '第 %d 次住院，起付线按 %s%% 递减为 %s 元' % (
                admission_index, round(factor * 100, 1), deductible)

        return {
            'deductible': deductible,
            'factor': factor,
            'base_deductible': round_money(base_deductible),
            'admission_index': admission_index,
            'note': note,
        }

    # ------------------------------------------------------------------
    def simulate(self, visits: Sequence[Mapping[str, Any]], *, city_code: str,
                 insurance_type: str = EMPLOYEE, visit_type: str = INPATIENT,
                 hospital_level: Optional[str] = None,
                 opening_state: Optional[YearState] = None,
                 class_ratios: Optional[Mapping[str, float]] = None,
                 year: int = 0) -> Dict[str, Any]:
        """模拟一年内的多次就医。

        Args:
            visits: 就医列表，每项形如::

                    {'amount': 30000, 'visit_type': 'inpatient',
                     'hospital_level': '三级医院', 'description': '第一次住院'}

                ``visit_type`` / ``hospital_level`` 可逐次覆盖默认值。
            city_code: 城市代码
            insurance_type: 险种
            visit_type: 默认就医类型
            hospital_level: 默认医院等级
            opening_state: 年初（或年中接入时的）已有累计状态
            class_ratios: 甲类 / 乙类 / 自费占比，默认 60% / 30% / 10%
            year: 医保年度，仅用于结果标注

        Returns:
            dict: 含 ``state``（年度累计）、``visits``（逐次明细）、
            ``savings_from_decreasing_deductible``（递减规则省下的钱）、``formula``

        Raises:
            PolicyNotFoundError: 城市 / 险种 / 就医类型政策不存在
            InvalidInputError: 就医列表为空或金额非法
        """
        visits = list(visits or [])
        if not visits:
            raise InvalidInputError('至少需要一次就医记录')

        ratios = dict(DEFAULT_CLASS_RATIOS)
        ratios.update(class_ratios or {})
        if abs(sum(ratios.values()) - 1.0) > 1e-6:
            raise InvalidInputError('甲类 / 乙类 / 自费占比之和必须为 1，当前为 %s'
                                    % round(sum(ratios.values()), 4))

        state = opening_state or YearState(year=year)
        state.year = year or state.year

        # 先校验政策存在，避免逐次循环里反复抛异常
        self.bundle.require_visit_policy(city_code, insurance_type, visit_type)

        records: List[VisitRecord] = []
        full_deductible_total = 0.0

        for index, raw in enumerate(visits, start=1):
            if raw.get('amount') is None:
                raise InvalidInputError('第 %d 次就医缺少 amount 字段' % index)
            amount = float(raw['amount'] or 0)
            if amount < 0:
                raise InvalidInputError('第 %d 次就医金额不能为负' % index)

            this_visit_type = raw.get('visit_type', visit_type) or visit_type
            this_level = raw.get('hospital_level', hospital_level)
            description = raw.get('description', '第 %d 次就医' % index)
            is_inpatient = this_visit_type == INPATIENT

            policy = self.bundle.require_visit_policy(city_code, insurance_type, this_visit_type)
            level, tiers = resolve_tiers(policy, this_level)
            table = TierTable(tiers)

            total_fee = round_money(amount)
            reimburse_base = round_money(amount * self._base_ratio(ratios))

            base_deductible = float(policy.get('deductible', 0) or 0)
            full_deductible_total = round_money(full_deductible_total + base_deductible)

            if is_inpatient and self.enabled():
                deductible_info = self.deductible_for(
                    city_code, state.admission_count + 1, base_deductible)
                deductible = deductible_info['deductible']
                deductible_note = deductible_info['note']
            else:
                deductible = round_money(base_deductible)
                deductible_note = '起付线 %s 元' % deductible

            above_deductible = round_money(max(0.0, reimburse_base - deductible))
            raw_reimburse = table.reimburse(above_deductible)

            remaining_quota = state.remaining_quota(policy.get('max_reimburse', 0))
            reimburse = round_money(min(raw_reimburse, remaining_quota))
            capped = raw_reimburse > remaining_quota

            personal_pay = round_money(total_fee - reimburse)

            # 大病保险按"年度累计个人自付"增量计算
            serious_before = self._serious_total(state.total_personal_pay)
            serious_after = self._serious_total(state.total_personal_pay + personal_pay)
            serious_this = round_money(max(0.0, serious_after - serious_before))

            record = VisitRecord(
                index=index,
                visit_type=this_visit_type,
                total_fee=total_fee,
                reimburse_base=reimburse_base,
                deductible=deductible,
                reimburse=reimburse,
                serious_reimburse=serious_this,
                personal_pay=round_money(personal_pay - serious_this),
                hospital_level=level,
                description=description,
                extra={
                    'deductible_note': deductible_note,
                    'capped_by_quota': capped,
                    'remaining_quota_before': remaining_quota,
                    'cumulative_personal_pay_after': round_money(
                        state.total_personal_pay + personal_pay),
                },
            )
            state.add_visit(record)
            records.append(record)

        # 递减规则省下的钱 = 每次都按全额起付线 vs 实际支付
        saved = round_money(max(0.0, full_deductible_total - state.deductible_paid))

        return {
            'state': state.to_dict(),
            'visits': [r.to_dict() for r in records],
            'savings_from_decreasing_deductible': saved,
            'insurance_type': insurance_type,
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'policy_year': self.bundle.year,
            'deductible_factors': self.deductible_factors(city_code),
            'formula': self._build_formula(state, saved, records),
        }

    # ------------------------------------------------------------------
    def compare_with_no_decrease(self, visits: Sequence[Mapping[str, Any]],
                                 **kwargs: Any) -> Dict[str, Any]:
        """对比"起付线递减"与"每次全额起付线"的差异。

        这是最能说明政策价值的对比：同样的就医安排，
        递减规则能省下多少起付线支出。
        """
        actual = self.simulate(visits, **kwargs)

        baseline = MultiAdmissionCalculator(self.bundle, decrease_deductible=False)
        plain = baseline.simulate(visits, **kwargs)

        return {
            'with_decreasing': actual['state'],
            'without_decreasing': plain['state'],
            'extra_reimburse': round_money(
                actual['state']['total_reimburse'] - plain['state']['total_reimburse']),
            'extra_saved_on_deductible': round_money(
                plain['state']['deductible_paid'] - actual['state']['deductible_paid']),
            'visits_detail': actual['visits'],
        }

    # ------------------------------------------------------------------
    def _serious_total(self, cumulative_personal_pay: float) -> float:
        """给定"年度累计个人自付"时，大病保险应报销的总额。"""
        if cumulative_personal_pay <= 0:
            return 0.0
        return self._serious.calculate(cumulative_personal_pay)['reimburse']

    @staticmethod
    def _base_ratio(ratios: Mapping[str, float]) -> float:
        """按甲乙自费占比得到"金额 → 报销基数"的整体折算系数。"""
        return (
            ratios.get('class_a', 0.0) * 1.0
            + ratios.get('class_b', 0.0) * 0.8
            + ratios.get('self_pay', 0.0) * 0.0
        )

    def _build_formula(self, state: YearState, saved: float,
                       records: Sequence[VisitRecord]) -> Dict[str, Any]:
        """生成年度汇总的公式说明。"""
        lines = ['【年度累计计算】']
        for record in records:
            lines.append(
                '第 %d 次：费用 %s 元，起付线 %s 元，报销 %s 元，自付 %s 元'
                % (record.index, record.total_fee, record.deductible,
                   record.total_reimburse, record.personal_pay))
        lines.append('全年合计：费用 %s 元，报销 %s 元，自付 %s 元，整体报销比例 %s%%'
                     % (state.total_fee, state.total_reimburse,
                        state.total_personal_pay, state.overall_ratio))
        if saved > 0:
            lines.append('因起付线递减规则，全年少付起付线约 %s 元' % saved)

        return {
            'steps': [
                {'key': 'total_fee', 'title': '年度总费用',
                 'expression': 'Σ 各次费用 = %s 元' % state.total_fee,
                 'result': state.total_fee},
                {'key': 'deductible_paid', 'title': '年度实际支付起付线',
                 'expression': '%s 元（%d 次住院）' % (state.deductible_paid, state.admission_count),
                 'result': state.deductible_paid},
                {'key': 'total_reimburse', 'title': '年度报销合计',
                 'expression': '%s 元' % state.total_reimburse,
                 'result': state.total_reimburse},
            ],
            'text': '\n'.join(lines),
            'saved_by_decreasing': saved,
        }
