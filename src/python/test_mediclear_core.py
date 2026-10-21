# -*- coding: utf-8 -*-
"""核心计算单元测试：分段表、费用模型、基本医保、大病保险、引擎。

配套说明见 ``README_python.md``。
"""

import pytest

from calculator import (
    InsufficientDataError,
    InvalidInputError,
    MediClearError,
    PolicyNotFoundError,
    quick_calculate,
)
from mediclear.constants import CLASS_A, CLASS_B, SELF_PAY
from mediclear.data import get_repository
from mediclear.engine import ReimbursementCalculator, compare_cities
from mediclear.models import FeeItem, normalize_fee_items
from mediclear.policies.base import TierTable, format_formula, resolve_tiers, round_money
from mediclear.policies.serious_illness import SeriousIllnessInsurance


# --------------------------------------------------------------------------
# TierTable：超额累进
# --------------------------------------------------------------------------
def test_tier_table_progressive():
    """分段报销是超额累进，不是"一刀切按最高档"。

    0~3 万报 85%，3~4 万报 90%，4 万以上报 95%，基数 5 万：
    30000×0.85 + 10000×0.90 + 10000×0.95 = 25500 + 9000 + 9500 = 44000
    """
    table = TierTable([
        {'min': 0, 'max': 30000, 'rate': 0.85},
        {'min': 30000, 'max': 40000, 'rate': 0.90},
        {'min': 40000, 'max': 300000, 'rate': 0.95},
    ])
    assert round_money(table.reimburse(50000)) == 44000.0
    detail = table.breakdown(50000)
    assert len(detail['segments']) == 3
    assert detail['segments'][0]['amount'] == 30000.0
    assert detail['segments'][2]['reimburse'] == 9500.0


def test_tier_table_within_first_tier():
    table = TierTable([{'min': 0, 'max': 30000, 'rate': 0.85}])
    assert table.reimburse(10000) == 8500.0
    assert table.breakdown(10000)['effective_rate'] == 0.85


def test_tier_table_marginal_rate_crosses_boundary():
    table = TierTable([
        {'min': 0, 'max': 30000, 'rate': 0.85},
        {'min': 30000, 'max': 40000, 'rate': 0.90},
    ])
    assert table.marginal_rate(30000) == 0.90
    assert table.min_rate == 0.85
    assert table.max_rate == 0.90


def test_tier_table_zero_and_negative_base():
    table = TierTable([{'min': 0, 'max': 30000, 'rate': 0.85}])
    assert table.reimburse(0) == 0.0
    assert table.reimburse(-500) == 0.0


def test_tier_table_filters_by_location():
    tiers = [
        {'min': 0, 'max': 10000, 'rate': 0.9, 'location': '一级医院'},
        {'min': 0, 'max': 10000, 'rate': 0.6, 'location': '三级医院'},
    ]
    assert TierTable(tiers, location='一级医院').reimburse(10000) == 9000.0
    # 不加 location 过滤则全部命中，两段相加
    assert TierTable(tiers).reimburse(10000) == 15000.0


def test_resolve_tiers_falls_back_to_default_level():
    """请求了政策里没有的医院等级时，回落到默认等级而不是报错。"""
    policy = {
        'default_hospital_level': '三级医院',
        'reimburse_rates': [
            {'min': 0, 'max': 10000, 'rate': 0.6, 'location': '三级医院'},
        ],
    }
    level, tiers = resolve_tiers(policy, '一级医院')
    assert level == '三级医院'
    assert len(tiers) == 1


def test_format_formula_keeps_placeholder_on_missing_key():
    assert format_formula('{a} + {b}', a=1) == '1 + {b}'


# --------------------------------------------------------------------------
# FeeItem / 费用明细归一化
# --------------------------------------------------------------------------
def test_fee_item_from_dict_resolves_item_type():
    item = FeeItem.from_dict({'name': '阿莫西林', 'amount': 120, 'item_type': '药品'})
    assert item.item_type == 'drug'
    # 药品默认归类到乙类
    assert item.category == 'class_b'
    assert item.to_dict()['item_type_name'] == '药品'


def test_fee_item_missing_category_defaults_to_class_a():
    """历史调用方只传 name/amount，必须仍然可用。"""
    items = normalize_fee_items([{'name': '西药费', 'amount': 100}])
    assert len(items) == 1
    assert items[0].category == CLASS_A
    assert items[0].amount == 100.0


def test_fee_item_amount_from_quantity_times_unit_price():
    item = FeeItem.from_dict({'name': 'CT', 'quantity': 2, 'unit_price': 300,
                              'item_type': 'exam'})
    assert item.amount == 600.0


def test_normalize_fee_items_accepts_bare_dict():
    items = normalize_fee_items({'name': '床位费', 'amount': 500, 'category': CLASS_A})
    assert isinstance(items, list) and len(items) == 1


def test_normalize_fee_items_rejects_bad_input():
    with pytest.raises(InvalidInputError):
        normalize_fee_items([{'amount': 'abc'}])
    with pytest.raises(InvalidInputError):
        normalize_fee_items('不是列表')


# --------------------------------------------------------------------------
# 基本医保
# --------------------------------------------------------------------------
def test_basic_calculation_matches_hand_computed_value():
    """北京职工住院三级：基数 8400，扣 1300 起付线，剩 7100 × 85% = 6035。"""
    result = quick_calculate('beijing', 'employee', 10000, 'inpatient', '三级医院')
    assert result['summary']['reimburse_base'] == 8400.0
    assert result['summary']['basic_reimburse'] == 6035.0


def test_category_split_drives_reimburse_base():
    """甲类全额计入，乙类按 80% 计入，自费不计入。"""
    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院')
    result = calc.calculate([
        {'name': '甲', 'amount': 1000, 'category': CLASS_A},
        {'name': '乙', 'amount': 1000, 'category': CLASS_B},
        {'name': '自费', 'amount': 1000, 'category': SELF_PAY},
    ])
    assert result['summary']['reimburse_base'] == 1800.0
    assert result['category_stats'][SELF_PAY]['total'] == 1000.0


def test_hospital_level_changes_reimburse():
    """一级医院报销比例应高于三级医院（引导分级诊疗）。"""
    calc_primary = ReimbursementCalculator('beijing', 'employee', 'inpatient', '一级医院')
    calc_tertiary = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院')
    items = [{'name': '费用', 'amount': 50000, 'category': CLASS_A}]
    assert (calc_primary.calculate(items)['summary']['total_reimburse']
            > calc_tertiary.calculate(items)['summary']['total_reimburse'])


def test_resident_reimburses_less_than_employee():
    employee = quick_calculate('beijing', 'employee', 30000, 'inpatient', '三级医院')
    resident = quick_calculate('beijing', 'resident', 30000, 'inpatient', '三级医院')
    assert (employee['summary']['total_reimburse']
            > resident['summary']['total_reimburse'])


def test_annual_quota_caps_basic_reimburse():
    """当年已累计报销接近封顶线时，**基本医保**报销被截断到剩余额度内。

    大病保险仍按个人自付单独计算，所以总额会高于剩余额度。
    """
    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院',
                                   year_accumulated=299000)
    result = calc.calculate([{'name': '费用', 'amount': 100000, 'category': CLASS_A}])
    assert result['summary']['basic_reimburse'] == 1000.0
    assert result['basic_details']['capped_by_quota'] is True
    assert result['basic_details']['remaining_quota'] == 1000.0
    # 封顶后个人自付变高，大病保险因此被触发
    assert result['summary']['serious_reimburse'] > 0


def test_zero_fee_items_returns_zeros():
    result = ReimbursementCalculator('beijing', 'employee').calculate([])
    assert result['summary']['total_fee'] == 0.0
    assert result['summary']['total_reimburse'] == 0.0
    assert result['summary']['reimburse_ratio'] == 0.0


def test_unknown_city_raises_policy_not_found():
    with pytest.raises(PolicyNotFoundError) as exc:
        ReimbursementCalculator('atlantis', 'employee')
    # 兼容旧代码里的 except ValueError
    assert isinstance(exc.value, ValueError)


def test_item_type_stats_group_by_item_type():
    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院')
    result = calc.calculate([
        {'name': '阿莫西林', 'amount': 400, 'item_type': '药品'},
        {'name': 'CT', 'amount': 600, 'item_type': '检查'},
    ])
    assert result['item_type_stats']['drug']['total'] == 400.0
    assert result['item_type_stats']['exam']['total'] == 600.0


# --------------------------------------------------------------------------
# 大病保险
# --------------------------------------------------------------------------
def test_serious_illness_below_threshold_reimburses_zero():
    module = SeriousIllnessInsurance(get_repository().current)
    result = module.calculate(5000)
    assert result['reimburse'] == 0.0
    assert result['eligible'] is False


def test_serious_illness_is_progressive_on_personal_pay():
    """大病保险按超额累进；个人自付越多，报销额增长越快。"""
    module = SeriousIllnessInsurance(get_repository().current)
    low = module.calculate(30000)['reimburse']
    high = module.calculate(200000)['reimburse']
    assert high > low > 0


def test_serious_illness_respects_annual_cap():
    module = SeriousIllnessInsurance(get_repository().current)
    result = module.calculate(10 ** 9)
    assert result['capped'] is True
    assert result['reimburse'] <= result['max_reimburse']


def test_engine_applies_serious_illness_on_top_of_basic():
    """大病保险在基本医保之后，按个人自付计算。"""
    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院')
    result = calc.calculate([{'name': '大额费用', 'amount': 400000, 'category': CLASS_A}])
    assert result['summary']['serious_reimburse'] > 0
    assert result['serious_illness']['above_deductible'] > 0


def test_serious_illness_can_be_disabled():
    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院')
    items = [{'name': '大额费用', 'amount': 400000, 'category': CLASS_A}]
    with_si = calc.calculate(items)
    without_si = calc.calculate(items, apply_serious_illness=False)
    assert without_si['summary']['serious_reimburse'] == 0.0
    assert (with_si['summary']['total_reimburse']
            > without_si['summary']['total_reimburse'])


# --------------------------------------------------------------------------
# 多城市对比
# --------------------------------------------------------------------------
def test_compare_cities_ranks_and_resolves_names():
    comparison = compare_cities(['beijing', 'shanghai', 'wuhan'], 'employee', 50000)
    rows = comparison['rows']
    assert len(rows) == 3
    assert [r['total_reimburse'] for r in rows] == sorted(
        [r['total_reimburse'] for r in rows], reverse=True)
    assert rows[0]['city_name'] == '北京市'
    assert comparison['spread'] == round_money(
        rows[0]['total_reimburse'] - rows[-1]['total_reimburse'])


def test_compare_cities_reports_unknown_city_as_error_row():
    comparison = compare_cities(['beijing', 'atlantis'], 'employee', 10000)
    errored = [r for r in comparison['rows'] if 'error' in r]
    assert len(errored) == 1
    assert errored[0]['city_code'] == 'atlantis'


# --------------------------------------------------------------------------
# 异常层次
# --------------------------------------------------------------------------
def test_exception_hierarchy():
    assert issubclass(PolicyNotFoundError, MediClearError)
    assert issubclass(InvalidInputError, ValueError)
    assert issubclass(InsufficientDataError, MediClearError)
    assert issubclass(MediClearError, Exception)


def test_negative_amount_rejected():
    with pytest.raises(InvalidInputError):
        normalize_fee_items([{'name': '费用', 'amount': -100}])
