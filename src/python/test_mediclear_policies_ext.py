# -*- coding: utf-8 -*-
"""扩展险种单元测试：门诊慢特病、个人账户、医保缴费、生育保险、家庭共济。"""

import pytest

from calculator import (
    calculate_chronic,
    calculate_contribution,
    calculate_maternity,
    calculate_personal_account,
)
from mediclear.constants import CLASS_A
from mediclear.data import get_repository
from mediclear.models import normalize_fee_items
from mediclear.policies.accounts import PersonalAccountPolicy
from mediclear.policies.chronic import ChronicDiseasePolicy
from mediclear.policies.contribution import ContributionPolicy
from mediclear.policies.household import HouseholdMutualAid
from mediclear.policies.maternity import MaternityInsurance


@pytest.fixture
def bundle():
    return get_repository().current


# --------------------------------------------------------------------------
# 门诊慢特病
# --------------------------------------------------------------------------
def test_chronic_diseases_include_hypertension_and_diabetes(bundle):
    codes = {d['code'] for d in ChronicDiseasePolicy(bundle).list_diseases()}
    assert {'hypertension', 'diabetes'} <= codes


def test_chronic_calculate_uses_disease_rate(bundle):
    """高血压门诊费用按病种比例报销，起付线通常为 0。"""
    module = ChronicDiseasePolicy(bundle)
    result = module.calculate(
        normalize_fee_items([{'name': '降压药', 'amount': 1000, 'category': CLASS_A}]),
        city_code='beijing', disease_code='hypertension')
    assert result['summary']['total_fee'] == 1000.0
    assert result['summary']['deductible'] == 0.0
    assert result['summary']['basic_reimburse'] == 800.0
    assert result['chronic_details']['disease_name'] == '高血压'


def test_chronic_requires_certification(bundle):
    """未备案时不予报销，并给出可读原因。"""
    result = calculate_chronic('beijing', 'hypertension', 1000, certified=False)
    assert result['eligible'] is False
    assert result['summary']['total_reimburse'] == 0.0
    assert '认定' in result['message'] or '备案' in result['message']
    assert result['explanation']


def test_chronic_annual_quota_caps_reimburse(bundle):
    """年度限额用完后再发生费用不再报销。"""
    module = ChronicDiseasePolicy(bundle)
    quota = module.resolve('hypertension', 'beijing')['annual_quota']
    result = module.calculate(
        normalize_fee_items([{'name': '药费', 'amount': quota * 4, 'category': CLASS_A}]),
        city_code='beijing', disease_code='hypertension')
    assert result['summary']['total_reimburse'] == pytest.approx(quota, abs=0.01)
    assert result['chronic_details']['capped_by_quota'] is True


def test_chronic_year_accumulated_reduces_remaining_quota(bundle):
    module = ChronicDiseasePolicy(bundle)
    quota = module.resolve('diabetes', 'beijing')['annual_quota']
    result = module.calculate(
        normalize_fee_items([{'name': '药费', 'amount': 100000, 'category': CLASS_A}]),
        city_code='beijing', disease_code='diabetes', year_accumulated=quota * 0.5)
    assert result['chronic_details']['remaining_quota'] == pytest.approx(quota * 0.5, abs=0.01)


def test_chronic_city_override_beats_default(bundle):
    """同一病种在不同城市可有不同的报销比例。"""
    module = ChronicDiseasePolicy(bundle)
    beijing = module.resolve('hypertension', 'beijing')
    wuhan = module.resolve('hypertension', 'wuhan')
    assert beijing['rate'] != wuhan['rate'] or beijing['annual_quota'] != wuhan['annual_quota']
    assert beijing.get('is_city_override') is True


def test_chronic_unknown_disease_raises(bundle):
    from mediclear.exceptions import PolicyNotFoundError
    with pytest.raises(PolicyNotFoundError):
        ChronicDiseasePolicy(bundle).resolve('not_a_disease', 'beijing')


def test_chronic_find_disease_by_keyword(bundle):
    matches = ChronicDiseasePolicy(bundle).find_disease('糖尿病')
    assert any(m['code'] == 'diabetes' for m in matches)


# --------------------------------------------------------------------------
# 个人账户
# --------------------------------------------------------------------------
def test_personal_account_monthly_credit(bundle):
    """在职人员月计入 = 缴费基数 × (个人 2% + 单位划入 0.8%)。"""
    result = PersonalAccountPolicy(bundle).project('beijing', 'employee', base_salary=10000,
                                                   months=12)
    assert result['monthly_credit'] == 280.0
    assert result['total_inflow'] == 3360.0
    assert result['closing_balance'] == 3360.0


def test_personal_account_base_is_clamped(bundle):
    """缴费基数低于下限时按下限计算。"""
    policy = PersonalAccountPolicy(bundle)
    limits = bundle.contribution_base_limits('beijing')
    low = policy.project('beijing', 'employee', base_salary=10, months=1)
    high = policy.project('beijing', 'employee', base_salary=10 ** 7, months=1)
    assert low['detail']['base'] == limits['min']
    assert high['detail']['base'] == limits['max']
    assert policy.clamp_base('beijing', 10)['clamped'] is True


def test_personal_account_retired_uses_flat_credit(bundle):
    result = PersonalAccountPolicy(bundle).project('beijing', 'employee', retired=True,
                                                   months=12)
    assert result['detail']['retired'] is True
    assert result['monthly_credit'] > 0


def test_personal_account_resident_has_no_account(bundle):
    """居民医保不设个人账户，月计入为 0。"""
    result = PersonalAccountPolicy(bundle).project('beijing', 'resident', months=12)
    assert result['detail']['supported'] is False
    assert result['monthly_credit'] == 0.0


def test_personal_account_projection_runs_out_of_balance(bundle):
    result = PersonalAccountPolicy(bundle).project(
        'beijing', 'employee', base_salary=10000, months=12, monthly_spend=1000)
    assert result['insufficient_month'] is not None
    assert result['closing_balance'] >= 0


def test_personal_account_public_function_matches(bundle):
    result = calculate_personal_account('beijing', base_salary=10000, months=12)
    assert result['monthly_credit'] == 280.0


# --------------------------------------------------------------------------
# 医保缴费
# --------------------------------------------------------------------------
def test_contribution_monthly_split(bundle):
    """职工医保：个人 2%，单位 8%，生育 0.8%。"""
    result = ContributionPolicy(bundle).monthly('beijing', 'employee', base_salary=10000)
    assert result['personal_monthly'] == 200.0
    assert result['employer_medical_monthly'] == 800.0
    assert result['maternity_monthly'] == 80.0
    assert result['total_monthly'] == 1080.0


def test_contribution_annual_is_twelve_times(bundle):
    module = ContributionPolicy(bundle)
    monthly = module.monthly('beijing', 'employee', base_salary=10000)
    annual = module.annual('beijing', 'employee', base_salary=10000)
    assert annual['personal_annual'] == monthly['personal_monthly'] * 12
    assert annual['total_annual'] == monthly['total_monthly'] * 12


def test_contribution_resident_is_annual_amount(bundle):
    """居民医保按年缴，个人缴费额由地方定额决定。"""
    result = ContributionPolicy(bundle).monthly('shanghai', 'resident')
    assert result['insurance_type'] == 'resident'
    assert result['total_annual'] > 0


def test_contribution_flexible_employment_can_include_account(bundle):
    with_account = ContributionPolicy(bundle).monthly(
        'beijing', 'employee', base_salary=10000, employment='flexible', with_account=True)
    without = ContributionPolicy(bundle).monthly(
        'beijing', 'employee', base_salary=10000, employment='flexible', with_account=False)
    assert with_account['personal_monthly'] > without['personal_monthly']
    assert without['employer_monthly'] == 0.0


def test_contribution_compare_cities(bundle):
    comparison = ContributionPolicy(bundle).compare_cities(
        ['beijing', 'shanghai', 'wuhan'], 'employee', base_salary=10000)
    assert comparison
    assert isinstance(comparison, (dict, list))


def test_contribution_public_function(bundle):
    result = calculate_contribution('beijing', 'employee', base_salary=10000)
    assert result['total_monthly'] == 1080.0
    assert result['formula']['text']


# --------------------------------------------------------------------------
# 生育保险
# --------------------------------------------------------------------------
def test_maternity_medical_reimburses_within_limit(bundle):
    result = MaternityInsurance(bundle).calculate('beijing', fees=3000)
    assert result['medical']['summary']['total_reimburse'] == 3000.0
    assert result['medical']['summary']['personal_pay'] == 0.0


def test_maternity_medical_caps_at_limit(bundle):
    """超过限额的部分自付。"""
    limit = MaternityInsurance(bundle).items('beijing')['normal_delivery']['limit']
    result = MaternityInsurance(bundle).calculate('beijing', fees=limit + 5000)
    assert result['medical']['summary']['total_reimburse'] == pytest.approx(limit, abs=0.01)
    assert result['medical']['summary']['personal_pay'] == pytest.approx(5000, abs=0.01)


def test_maternity_allowance_days_include_local_leave(bundle):
    module = MaternityInsurance(bundle)
    with_leave = module.allowance_days('beijing', 'normal_delivery', 1, True)
    without = module.allowance_days('beijing', 'normal_delivery', 1, False)
    assert with_leave['total_days'] > without['total_days']
    assert without['total_days'] == with_leave['min_days'] == 98


def test_maternity_twins_add_days(bundle):
    single = MaternityInsurance(bundle).allowance_days('beijing', 'normal_delivery', 1)
    twins = MaternityInsurance(bundle).allowance_days('beijing', 'normal_delivery', 2)
    assert twins['total_days'] > single['total_days']


def test_maternity_allowance_amount(bundle):
    """津贴 = 单位月平均工资 / 30 × 产假天数。"""
    result = MaternityInsurance(bundle).calculate_allowance('beijing', 10000,
                                                           delivery_type='normal_delivery')
    expected = round(10000 / 30.0 * result['days']['total_days'], 2)
    assert result['allowance'] == pytest.approx(expected, abs=0.02)


def test_maternity_cesarean_more_than_normal(bundle):
    module = MaternityInsurance(bundle)
    normal = module.calculate_allowance('beijing', 10000, delivery_type='normal_delivery')
    cesarean = module.calculate_allowance('beijing', 10000, delivery_type='cesarean')
    assert cesarean['allowance'] > normal['allowance']


def test_maternity_employer_makes_up_wage_gap(bundle):
    """本人工资高于单位平均工资时，差额由单位补足。"""
    result = MaternityInsurance(bundle).calculate_allowance(
        'beijing', 6000, personal_monthly_wage=12000)
    assert result['makeup_by_employer'] > 0
    assert result['total_benefit'] >= result['allowance']


def test_maternity_public_function(bundle):
    result = calculate_maternity('beijing', fees=3000, monthly_avg_wage=10000)
    assert 'medical' in result and 'allowance' in result


# --------------------------------------------------------------------------
# 家庭共济
# --------------------------------------------------------------------------
def test_household_pays_member_self_pay(bundle):
    result = HouseholdMutualAid(bundle).calculate(2000, 5000)
    assert result['payable'] == 2000.0
    assert result['member_out_of_pocket'] == 0.0
    assert result['account_balance_after'] == 3000.0


def test_household_limited_by_account_balance(bundle):
    result = HouseholdMutualAid(bundle).calculate(8000, 5000)
    assert result['payable'] == 5000.0
    assert result['member_out_of_pocket'] == 3000.0
    assert result['account_balance_after'] == 0.0


def test_household_blocks_disallowed_usage(bundle):
    module = HouseholdMutualAid(bundle)
    result = module.calculate(1000, 5000, usage='cosmetic_surgery')
    assert result['usage_allowed'] is False
    assert result['payable'] == 0.0


def test_household_allocate_proportional_splits_evenly(bundle):
    result = HouseholdMutualAid(bundle).allocate(
        3000, [{'name': '父亲', 'self_pay': 2000}, {'name': '孩子', 'self_pay': 2000}],
        strategy='proportional')
    assert result['total_payable'] == 3000.0
    assert result['shortfall'] == 1000.0
    # 需求相同 → 各拿一半
    assert [a['payable'] for a in result['allocations']] == [1500.0, 1500.0]


def test_household_allocate_priority_fills_in_order(bundle):
    result = HouseholdMutualAid(bundle).allocate(
        3000, [{'name': '父亲', 'self_pay': 2000}, {'name': '孩子', 'self_pay': 2000}],
        strategy='priority')
    allocations = result['allocations']
    assert allocations[0]['payable'] == 2000.0
    assert allocations[1]['payable'] == 1000.0


def test_household_allocate_requires_self_pay(bundle):
    from mediclear.exceptions import InvalidInputError
    with pytest.raises(InvalidInputError):
        HouseholdMutualAid(bundle).allocate(3000, [{'name': '父亲', 'amount': 2000}])


def test_household_validate_binding_rejects_too_many_members(bundle):
    module = HouseholdMutualAid(bundle)
    owner = {'name': '本人', 'insurance_type': 'employee'}
    members = [{'name': '亲人%d' % i, 'insurance_type': 'employee'} for i in range(50)]
    result = module.validate_binding(owner, members)
    assert result['valid'] is False
