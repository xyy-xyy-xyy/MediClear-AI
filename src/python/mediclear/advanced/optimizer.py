# -*- coding: utf-8 -*-
"""年度最优报销策略推荐。

回答用户最关心的两个"要不要"问题：

* **"年底要不要再看一次病？"** —— :meth:`AnnualStrategyOptimizer.advise_timing`
* **"这笔治疗怎么安排最省钱？"** —— :meth:`AnnualStrategyOptimizer.recommend`

做法不是拍脑袋给建议，而是把几种安排各自**完整模拟一遍**，
按"个人实际支出"排序，用数据说话。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..constants import EMPLOYEE, INPATIENT
from ..data.repository import PolicyBundle
from ..exceptions import InvalidInputError
from ..policies.base import round_money
from .multi_admission import DEFAULT_CLASS_RATIOS, MultiAdmissionCalculator
from .year_state import YearState

__all__ = ['AnnualStrategyOptimizer']


class AnnualStrategyOptimizer:
    """年度就医安排的策略推荐。

    Args:
        bundle: 政策数据包
    """

    def __init__(self, bundle: PolicyBundle):
        self.bundle = bundle

    # ------------------------------------------------------------------
    def recommend(self, city_code: str, insurance_type: str, planned_amount: float, *,
                  visit_type: str = INPATIENT,
                  hospital_level: Optional[str] = None,
                  year_state: Optional[YearState] = None,
                  class_ratios: Optional[Mapping[str, float]] = None,
                  alternative_hospital_level: Optional[str] = None) -> Dict[str, Any]:
        """对一笔计划中的医疗支出，比较几种安排方式的个人支出。

        比较的方案：

        ============  ====================================================
        方案          说明
        ============  ====================================================
        ``now_once``  本年度一次性做完
        ``split_year``本年度分两次做
        ``cross_year``本年度做一半，剩下的明年年初做
        ``lower_level``换到更低等级医院一次做完（需指定等级）
        ============  ====================================================

        Args:
            city_code: 城市代码
            insurance_type: 险种
            planned_amount: 计划中的医疗总费用
            visit_type: 就医类型
            hospital_level: 医院等级
            year_state: 当前年度累计状态（不传则视为年初全新状态）
            class_ratios: 甲 / 乙 / 自费占比
            alternative_hospital_level: 用于 ``lower_level`` 方案的替代医院等级，
                例如 ``'二级医院'``

        Returns:
            ``{'options': [...], 'recommended': {...}, 'max_saving': float, 'advice': [str]}``
        """
        if planned_amount <= 0:
            raise InvalidInputError('计划金额必须为正数，收到 %r' % planned_amount)

        state = year_state or YearState()
        ratios = dict(DEFAULT_CLASS_RATIOS)
        ratios.update(class_ratios or {})

        sim = MultiAdmissionCalculator(self.bundle)
        options: List[Dict[str, Any]] = []

        # --- 方案 1：本年度一次性做完 ------------------------------------
        once = sim.simulate([{'amount': planned_amount, 'description': '本年度一次完成'}],
                            city_code=city_code, insurance_type=insurance_type,
                            visit_type=visit_type, hospital_level=hospital_level,
                            opening_state=_copy_state(state), class_ratios=ratios)
        options.append(self._option(
            'now_once', '本年度一次性完成', once,
            '全部费用在本年度结算，起付线与封顶线按当前年度累计计算'))

        # --- 方案 2：本年度分两次 ----------------------------------------
        half = round_money(planned_amount / 2)
        split = sim.simulate(
            [{'amount': half, 'description': '本年度第一次'},
             {'amount': round_money(planned_amount - half), 'description': '本年度第二次'}],
            city_code=city_code, insurance_type=insurance_type,
            visit_type=visit_type, hospital_level=hospital_level,
            opening_state=_copy_state(state), class_ratios=ratios)
        options.append(self._option(
            'split_year', '本年度分两次完成', split,
            '分次就医可享受起付线递减，但要承担两次就医的时间成本'))

        # --- 方案 3：跨年度拆分 ------------------------------------------
        first_year = sim.simulate(
            [{'amount': half, 'description': '本年度部分'}],
            city_code=city_code, insurance_type=insurance_type,
            visit_type=visit_type, hospital_level=hospital_level,
            opening_state=_copy_state(state), class_ratios=ratios)
        next_year = sim.simulate(
            [{'amount': round_money(planned_amount - half), 'description': '明年部分'}],
            city_code=city_code, insurance_type=insurance_type,
            visit_type=visit_type, hospital_level=hospital_level,
            opening_state=YearState(year=state.year + 1), class_ratios=ratios)
        options.append(self._option(
            'cross_year', '本年度做一半，明年再做一半',
            _merge_simulations(first_year, next_year),
            '跨年度拆分可重新获得年度封顶线额度，但明年需重新计算起付线'))

        # --- 方案 4：换到更低等级医院 ------------------------------------
        if alternative_hospital_level and alternative_hospital_level != hospital_level:
            cheaper = sim.simulate(
                [{'amount': planned_amount, 'description': '在%s完成' % alternative_hospital_level}],
                city_code=city_code, insurance_type=insurance_type,
                visit_type=visit_type, hospital_level=alternative_hospital_level,
                opening_state=_copy_state(state), class_ratios=ratios)
            options.append(self._option(
                'lower_level', '改到%s就医' % alternative_hospital_level, cheaper,
                '低等级医院报销比例更高，但需结合病情由医生判断是否可行'))

        options.sort(key=lambda o: o['total_personal_pay'])
        best = options[0]
        worst = options[-1]

        advice = self._build_advice(options, state, visit_type)

        return {
            'planned_amount': round_money(planned_amount),
            'city_code': city_code,
            'city_name': self.bundle.city(city_code).get('name', city_code),
            'options': options,
            'recommended': best,
            'worst': worst,
            'max_saving': round_money(worst['total_personal_pay'] - best['total_personal_pay']),
            'advice': advice,
            'opening_state': state.to_dict(),
            'disclaimer': '以上为基于当前政策的模拟测算，实际就医安排请结合医生建议，'
                          '最终报销以医保部门核定为准。',
        }

    # ------------------------------------------------------------------
    def advise_timing(self, city_code: str, insurance_type: str, amount: float, *,
                      visit_type: str = INPATIENT,
                      hospital_level: Optional[str] = None,
                      year_state: Optional[YearState] = None,
                      class_ratios: Optional[Mapping[str, float]] = None) -> Dict[str, Any]:
        """"年底要不要再看一次病？"——比较今年做与明年做的个人支出。

        Returns:
            ``{'do_this_year': {...}, 'do_next_year': {...}, 'recommendation': str,
            'difference': float, 'reasons': [str]}``
        """
        state = year_state or YearState()
        ratios = dict(DEFAULT_CLASS_RATIOS)
        ratios.update(class_ratios or {})
        sim = MultiAdmissionCalculator(self.bundle)

        this_year = sim.simulate(
            [{'amount': amount, 'description': '本年度就医'}],
            city_code=city_code, insurance_type=insurance_type,
            visit_type=visit_type, hospital_level=hospital_level,
            opening_state=_copy_state(state), class_ratios=ratios)
        next_year = sim.simulate(
            [{'amount': amount, 'description': '下一年度就医'}],
            city_code=city_code, insurance_type=insurance_type,
            visit_type=visit_type, hospital_level=hospital_level,
            opening_state=YearState(year=state.year + 1), class_ratios=ratios)

        this_pay = this_year['state']['total_personal_pay']
        next_pay = next_year['state']['total_personal_pay']
        # 今年做的话，今年的累计里还包含此前已发生的支出，要减掉只看增量
        baseline_pay = state.total_personal_pay
        this_increment = round_money(this_pay - baseline_pay)

        difference = round_money(this_increment - next_pay)
        reasons: List[str] = []

        if difference < -0.005:
            recommendation = 'this_year'
            reasons.append('本年度已经支付过起付线，今年再做不用重复承担起付线，'
                           '比等到明年做少花约 %s 元。' % abs(difference))
        elif difference > 0.005:
            recommendation = 'next_year'
            reasons.append('本年度接近或已用满封顶线，'
                           '把治疗安排到明年可重新获得额度，少花约 %s 元。' % difference)
        else:
            recommendation = 'either'
            reasons.append('今年做和明年做的个人支出基本相同，按病情需要安排即可。')

        if state.admission_count > 0:
            reasons.append('本年度已住院 %d 次，起付线已累计支付 %s 元。'
                           % (state.admission_count, state.deductible_paid))
        if state.total_reimburse > 0:
            reasons.append('本年度已报销 %s 元。' % state.total_reimburse)

        return {
            'amount': round_money(amount),
            'do_this_year': {
                'increment_personal_pay': this_increment,
                'state_after': this_year['state'],
                'visits': this_year['visits'],
            },
            'do_next_year': {
                'personal_pay': next_pay,
                'state_after': next_year['state'],
                'visits': next_year['visits'],
            },
            'difference': difference,
            'recommendation': recommendation,
            'reasons': reasons,
            'disclaimer': '测算仅供参考，实际就医时间请遵医嘱。',
        }

    # ------------------------------------------------------------------
    def _option(self, key: str, label: str, simulation: Mapping[str, Any],
                note: str) -> Dict[str, Any]:
        """把一次模拟结果包装成一个候选方案。"""
        state = simulation['state']
        return {
            'key': key,
            'label': label,
            'note': note,
            'total_fee': state['total_fee'],
            'total_reimburse': state['total_reimburse'],
            'total_personal_pay': state['total_personal_pay'],
            'deductible_paid': state['deductible_paid'],
            'admission_count': state['admission_count'],
            'overall_ratio': state['overall_ratio'],
            'visit_count': state.get('visit_count', len(simulation.get('visits', []))),
            'visits': simulation.get('visits', []),
        }

    def _build_advice(self, options: Sequence[Mapping[str, Any]], state: YearState,
                      visit_type: str) -> List[str]:
        """把方案差异翻译成人话建议。"""
        advice: List[str] = []
        if not options:
            return advice

        best = options[0]
        advice.append('推荐「%s」：预计个人支出最低，约 %s 元。'
                      % (best['label'], best['total_personal_pay']))

        for option in options[1:]:
            gap = round_money(option['total_personal_pay'] - best['total_personal_pay'])
            if gap > 0:
                advice.append('「%s」比推荐方案多花约 %s 元。' % (option['label'], gap))

        if state.admission_count == 0 and visit_type == INPATIENT:
            advice.append('本年度还没住过院，本次将支付全额起付线；'
                          '若年内还需再次住院，第二次起付线会递减。')
        if state.deductible_paid > 0:
            advice.append('本年度已累计支付起付线 %s 元。' % state.deductible_paid)

        return advice


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------
def _copy_state(state: YearState) -> YearState:
    """复制年度状态，避免模拟过程污染调用方的对象。"""
    clone = YearState.from_dict(state.to_dict())
    clone.visits = list(state.visits)
    return clone


def _merge_simulations(first: Mapping[str, Any], second: Mapping[str, Any]) -> Dict[str, Any]:
    """把两次模拟（今年 / 明年）合并成一个可比方案。"""
    a, b = first['state'], second['state']
    total_fee = round_money(a['total_fee'] + b['total_fee'])
    total_reimburse = round_money(a['total_reimburse'] + b['total_reimburse'])
    total_personal = round_money(a['total_personal_pay'] + b['total_personal_pay'])

    return {
        'state': {
            'year': a['year'],
            'total_fee': total_fee,
            'total_reimburse': total_reimburse,
            'total_personal_pay': total_personal,
            'deductible_paid': round_money(a['deductible_paid'] + b['deductible_paid']),
            'admission_count': a['admission_count'] + b['admission_count'],
            'outpatient_count': a['outpatient_count'] + b['outpatient_count'],
            'visit_count': a.get('visit_count', 0) + b.get('visit_count', 0),
            'overall_ratio': round(total_reimburse / total_fee * 100, 1) if total_fee else 0.0,
        },
        'visits': list(first.get('visits', [])) + list(second.get('visits', [])),
    }
