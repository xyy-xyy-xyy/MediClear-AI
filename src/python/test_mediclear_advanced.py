# -*- coding: utf-8 -*-
"""高级计算单元测试：年度累计、多次住院起付线递减、年度最优策略、方案对比。"""

import copy

import pytest

from mediclear.advanced.comparison import ComparisonAnalyzer
from mediclear.advanced.multi_admission import MultiAdmissionCalculator
from mediclear.advanced.optimizer import AnnualStrategyOptimizer
from mediclear.advanced.year_state import VisitRecord, YearState
from mediclear.data import get_repository
from mediclear.exceptions import InvalidInputError


@pytest.fixture
def bundle():
    return get_repository().current


@pytest.fixture
def visits():
    return [{'amount': 20000, 'description': '第一次住院'},
            {'amount': 20000, 'description': '第二次住院'},
            {'amount': 30000, 'description': '第三次住院'}]


# --------------------------------------------------------------------------
# YearState
# --------------------------------------------------------------------------
def test_year_state_accumulates_visits():
    state = YearState(year=2026)
    state.add_visit(VisitRecord(index=1, visit_type='inpatient', total_fee=10000,
                                reimburse_base=8000, deductible=1300,
                                reimburse=5000, serious_reimburse=0, personal_pay=5000))
    assert state.total_fee == 10000.0
    assert state.total_reimburse == 5000.0
    assert state.deductible_paid == 1300.0
    assert state.admission_count == 1


def test_year_state_counts_outpatient_separately():
    state = YearState(year=2026)
    state.add_visit(VisitRecord(index=1, visit_type='outpatient', total_fee=500,
                                reimburse_base=500, deductible=0, reimburse=300,
                                serious_reimburse=0, personal_pay=200))
    assert state.outpatient_count == 1
    assert state.admission_count == 0


def test_year_state_remaining_quota():
    state = YearState(year=2026, total_reimburse=250000)
    assert state.remaining_quota(300000) == 50000.0
    assert state.remaining_quota(200000) == 0.0


def test_year_state_round_trip_serialization():
    state = YearState(year=2026, total_fee=10000, total_reimburse=6000,
                      total_personal_pay=4000, deductible_paid=1300, admission_count=1)
    restored = YearState.from_dict(state.to_dict())
    assert restored.total_reimburse == state.total_reimburse
    assert restored.admission_count == state.admission_count


# --------------------------------------------------------------------------
# 起付线递减
# --------------------------------------------------------------------------
def test_deductible_factors_decrease(bundle):
    factors = MultiAdmissionCalculator(bundle).deductible_factors('beijing')
    assert factors[0] == 1.0
    assert factors[1] < factors[0]
    assert factors[-1] == 0.0


def test_deductible_for_each_admission(bundle):
    module = MultiAdmissionCalculator(bundle)
    first = module.deductible_for('beijing', 1, 1300)
    second = module.deductible_for('beijing', 2, 1300)
    third = module.deductible_for('beijing', 3, 1300)
    assert first['deductible'] == 1300.0
    assert second['deductible'] == 650.0
    assert third['deductible'] == 0.0
    assert '第 1 次住院' in first['note']


def test_deductible_index_must_start_at_one(bundle):
    with pytest.raises(InvalidInputError):
        MultiAdmissionCalculator(bundle).deductible_for('beijing', 0, 1300)


def test_simulate_three_admissions(bundle, visits):
    result = MultiAdmissionCalculator(bundle).simulate(visits, city_code='beijing', year=2026)
    assert len(result['visits']) == 3
    # 起付线逐次递减：1300 + 650 + 0
    assert [v['deductible'] for v in result['visits']] == [1300.0, 650.0, 0.0]
    assert result['state']['deductible_paid'] == 1950.0
    assert result['state']['total_fee'] == 70000.0
    assert result['state']['admission_count'] == 3
    assert result['savings_from_decreasing_deductible'] == 1950.0


def test_simulate_requires_amount(bundle):
    with pytest.raises(InvalidInputError):
        MultiAdmissionCalculator(bundle).simulate([{'total_fee': 1000}],
                                                  city_code='beijing')


def test_simulate_rejects_empty_visit_list(bundle):
    with pytest.raises(InvalidInputError):
        MultiAdmissionCalculator(bundle).simulate([], city_code='beijing')


def test_simulate_rejects_bad_class_ratios(bundle):
    with pytest.raises(InvalidInputError):
        MultiAdmissionCalculator(bundle).simulate(
            [{'amount': 1000}], city_code='beijing',
            class_ratios={'class_a': 0.5, 'class_b': 0.5, 'self_pay': 0.5})


def test_compare_with_no_decrease_shows_the_saving(bundle, visits):
    module = MultiAdmissionCalculator(bundle)
    comparison = module.compare_with_no_decrease(visits, city_code='beijing', year=2026)
    assert comparison['extra_saved_on_deductible'] == 1950.0
    assert comparison['extra_reimburse'] > 0
    assert (comparison['with_decreasing']['total_reimburse']
            > comparison['without_decreasing']['total_reimburse'])


def test_compare_with_no_decrease_does_not_mutate_bundle(bundle, visits):
    """基线方案不能把共享的政策数据改坏（否则后续计算全错）。"""
    before = copy.deepcopy(bundle.multi_admission)
    module = MultiAdmissionCalculator(bundle)
    module.compare_with_no_decrease(visits, city_code='beijing', year=2026)
    assert bundle.multi_admission == before
    # 递减仍在生效
    assert module.deductible_for('beijing', 2, 1300)['deductible'] == 650.0


def test_simulate_respects_opening_state(bundle):
    """年中接入时，年初已累计的起付线应让本次起付线继续递减。"""
    opening = YearState(year=2026, admission_count=1, deductible_paid=1300)
    result = MultiAdmissionCalculator(bundle).simulate(
        [{'amount': 20000}], city_code='beijing', opening_state=opening, year=2026)
    assert result['visits'][0]['deductible'] == 650.0
    assert result['state']['admission_count'] == 2


# --------------------------------------------------------------------------
# 年度最优策略
# --------------------------------------------------------------------------
def test_recommend_returns_ranked_options(bundle):
    result = AnnualStrategyOptimizer(bundle).recommend('beijing', 'employee', 30000)
    keys = [o['key'] for o in result['options']]
    assert 'now_once' in keys and 'split_year' in keys
    assert result['recommended']['key'] in keys
    assert result['max_saving'] >= 0
    assert result['advice']
    # 所有方案总额相同，差别只在报销额
    assert all(o['total_fee'] == 30000.0 for o in result['options'])


def test_recommend_picks_lowest_personal_pay(bundle):
    result = AnnualStrategyOptimizer(bundle).recommend('beijing', 'employee', 30000)
    pays = [o['total_personal_pay'] for o in result['options']]
    assert result['recommended']['total_personal_pay'] == min(pays)


def test_advise_timing_prefers_this_year_when_deductible_paid(bundle):
    """已交过起付线时，今年再看一次病更划算（起付线递减 + 免重复承担）。

    ``difference`` = 今年做的自付增量 − 放到明年的自付，为负说明今年更省。
    """
    state = YearState(year=2026, total_fee=20000, total_reimburse=13175,
                      total_personal_pay=6825, deductible_paid=1300, admission_count=1)
    advice = AnnualStrategyOptimizer(bundle).advise_timing(
        'beijing', 'employee', 30000, year_state=state)
    assert advice['recommendation'] == 'this_year'
    assert advice['difference'] < 0
    assert any('起付线' in r for r in advice['reasons'])


def test_advise_timing_is_neutral_without_prior_visits(bundle):
    advice = AnnualStrategyOptimizer(bundle).advise_timing('beijing', 'employee', 30000)
    assert advice['recommendation'] == 'either'
    assert advice['difference'] == 0.0


def test_advise_timing_includes_disclaimer(bundle):
    advice = AnnualStrategyOptimizer(bundle).advise_timing('beijing', 'employee', 10000)
    assert '遵医嘱' in advice['disclaimer']


# --------------------------------------------------------------------------
# 方案对比
# --------------------------------------------------------------------------
def test_compare_cities_ranks_by_reimburse(bundle):
    result = ComparisonAnalyzer(bundle).compare_cities(['beijing', 'shanghai', 'wuhan'], 50000)
    totals = [r['total_reimburse'] for r in result['rows']]
    assert totals == sorted(totals, reverse=True)
    assert result['best']['total_reimburse'] == totals[0]
    assert result['worst']['total_reimburse'] == totals[-1]


def test_compare_hospital_levels_differentiates(bundle):
    """一级医院应比三级医院报得多——否则分级诊疗的政策导向就体现不出来。"""
    result = ComparisonAnalyzer(bundle).compare_hospital_levels('beijing', 50000)
    by_label = {r['label']: r['total_reimburse'] for r in result['rows']}
    assert by_label['一级医院'] > by_label['三级医院']
    assert result['dimension'] == 'hospital_level'


def test_compare_insurance_types(bundle):
    result = ComparisonAnalyzer(bundle).compare_insurance_types('beijing', 30000)
    by_label = {r['insurance_type']: r for r in result['rows']}
    assert (by_label['employee']['total_reimburse']
            > by_label['resident']['total_reimburse'])


def test_compare_chronic_certification_shows_the_gain(bundle):
    """办不办慢特病认定，报销差多少——这是最有说服力的数字。"""
    result = ComparisonAnalyzer(bundle).compare_chronic_certification(
        'beijing', 'hypertension', 5000)
    assert len(result['rows']) == 2
    assert result['best']['total_reimburse'] > result['worst']['total_reimburse']
    assert result['spread'] > 0


def test_compare_relocation_covers_all_record_types(bundle):
    result = ComparisonAnalyzer(bundle).compare_relocation(
        'wuhan', 'shanghai', 30000, record_types=('long_term', 'temp', 'transfer', 'none'))
    assert len(result['rows']) == 4
    by_label = {r['label']: r['total_reimburse'] for r in result['rows']}
    assert by_label['长期驻外备案'] > by_label['未备案']


def test_compare_scenarios_handles_invalid_row(bundle):
    result = ComparisonAnalyzer(bundle).compare_scenarios([
        {'label': '正常', 'city_code': 'beijing', 'amount': 10000},
        {'label': '缺金额', 'city_code': 'beijing'},
        {'label': '坏城市', 'city_code': 'atlantis', 'amount': 10000},
    ])
    valid = [r for r in result['rows'] if 'error' not in r]
    broken = [r for r in result['rows'] if 'error' in r]
    assert len(valid) == 1
    assert len(broken) == 2
    assert result['summary']
    assert result['baseline']['label'] == '正常'


def test_comparison_uses_its_own_policy_version():
    """对比分析器必须用自己那份 bundle 的版本，不能偷偷读全局默认版本。"""
    repository = get_repository()
    bundle_2025 = repository.load(2025)
    result = ComparisonAnalyzer(bundle_2025).compare_cities(['beijing'], 50000)
    current = repository.load(2026)
    result_2026 = ComparisonAnalyzer(current).compare_cities(['beijing'], 50000)
    assert (result['rows'][0]['total_reimburse']
            < result_2026['rows'][0]['total_reimburse'])
