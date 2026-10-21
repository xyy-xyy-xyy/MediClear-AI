# -*- coding: utf-8 -*-
"""异地就医单元测试：备案类型调整、与本地就医的对比、材料清单。"""

import pytest

from mediclear.constants import CLASS_A, CLASS_B, SELF_PAY
from mediclear.engine import ReimbursementCalculator
from relocation import (
    CITY_POLICIES,
    FEE_CATEGORIES,
    RECORD_TYPES,
    RELOCATION_POLICY,
    SERIOUS_ILLNESS_INSURANCE,
    RelocationCalculator,
    generate_checklist,
)


def test_relocation_exposes_legacy_module_constants():
    """老代码 ``from relocation import CITY_POLICIES`` 必须继续可用。"""
    assert CITY_POLICIES
    assert FEE_CATEGORIES
    assert RELOCATION_POLICY
    assert SERIOUS_ILLNESS_INSURANCE


def test_record_types_cover_the_four_备案_types():
    assert set(RECORD_TYPES) == {'long_term', 'temp', 'transfer', 'none'}
    for info in RECORD_TYPES.values():
        assert info['name'] and info['adjust_desc']


def test_long_term_record_keeps_local_rates():
    """长期驻外备案不降比例：异地报销额 = 参保地本地报销额。"""
    calc = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient', 'long_term')
    result = calc.calculate(20000)
    assert result['summary']['rateAdjust'] == 0.0
    assert result['summary']['basicReimburse'] == result['comparison']['localReimburse']
    assert result['comparison']['difference'] == 0.0


def test_unrecorded_visit_loses_the_most():
    long_term = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                     'long_term').calculate(20000)
    none = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                'none').calculate(20000)
    assert none['summary']['finalRate'] < long_term['summary']['finalRate']
    assert none['summary']['basicReimburse'] < long_term['summary']['basicReimburse']


def test_record_type_adjustment_monotonic():
    """报销额必须随备案方式的降幅单调递减：长期驻外 > 临时 > 转诊 > 未备案。——
    临时与转诊的降幅分别为 5% 与 8%，顺序固定。
    """
    amounts = [
        RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient', record)
        .calculate(50000)['summary']['basicReimburse']
        for record in ('long_term', 'temp', 'transfer', 'none')
    ]
    assert amounts == sorted(amounts, reverse=True)
    assert len(set(amounts)) == 4


def test_adjustment_is_applied_to_final_rate():
    calc = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient', 'transfer')
    result = calc.calculate(20000)
    assert result['summary']['rateAdjust'] == -8.0
    assert result['summary']['finalRate'] == pytest.approx(
        result['summary']['baseRate'] - 8.0, abs=0.01)


def test_comparison_difference_is_never_a_stale_zero():
    """对比差额必须反映真实损失（早期的实现恒为 0）。"""
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'none').calculate(30000)
    assert result['comparison']['difference'] > 0
    assert result['comparison']['difference'] == pytest.approx(
        result['comparison']['localReimburse'] - result['summary']['basicReimburse'],
        abs=0.01)


def test_unrecorded_tips_warn_about_the_loss():
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'none').calculate(20000)
    assert any('未办理' in tip for tip in result['tips'])


def test_long_term_tips_have_no_warning():
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'long_term').calculate(20000)
    assert not any('未办理' in tip for tip in result['tips'])
    assert len(result['tips']) >= 4


def test_relocation_formula_documents_every_step():
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'temp').calculate(120000)
    text = result['formula']['text']
    assert '异地就医' in text
    assert '备案' in text
    assert len(result['formula']['steps']) >= 5
    for step in result['formula']['steps']:
        assert step['title'] and step['expression']


def test_relocation_uses_tiered_rates_for_large_bills():
    """大额费用跨档时，公式里要出现多段算式。"""
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'long_term').calculate(120000)
    assert result['formula']['text'].count('×') >= 2


def test_relocation_reimburse_base_matches_local_engine():
    """异地就医的报销基数口径应与本地引擎一致（同为按类别折算）。"""
    items = [
        {'name': '甲类', 'amount': 10000, 'category': CLASS_A},
        {'name': '乙类', 'amount': 5000, 'category': CLASS_B},
        {'name': '自费', 'amount': 2000, 'category': SELF_PAY},
    ]
    relocation = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                      'long_term').calculate(0, fee_items=items)
    local = ReimbursementCalculator('wuhan', 'employee', 'inpatient', '三级医院').calculate(items)
    assert relocation['summary']['reimburseBase'] == local['summary']['reimburse_base']
    assert relocation['summary']['basicReimburse'] == local['summary']['basic_reimburse']


def test_relocation_accepts_fee_items_over_total():
    """给了明细就以明细为准，total_fee 参数被忽略。"""
    items = [{'name': '甲类', 'amount': 1000, 'category': CLASS_A}]
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'long_term').calculate(99999, fee_items=items)
    assert result['summary']['totalFee'] == 1000.0


def test_relocation_unknown_city_raises_value_error():
    with pytest.raises(ValueError):
        RelocationCalculator('atlantis', 'shanghai')
    with pytest.raises(ValueError):
        RelocationCalculator('wuhan', 'atlantis')


def test_relocation_unknown_insurance_type_raises():
    with pytest.raises(ValueError):
        RelocationCalculator('wuhan', 'shanghai', 'dragon')


def test_relocation_zero_fee():
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'long_term').calculate(0)
    assert result['summary']['totalReimburse'] == 0.0
    assert result['summary']['reimburseRatio'] == 0.0


def test_relocation_big_bill_triggers_serious_illness():
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'none').calculate(800000)
    assert result['summary']['seriousReimburse'] > 0
    assert result['seriousIllness']['eligible'] is True


def test_relocation_result_keeps_camel_case_shape():
    """前端契约：summary 用 camelCase 键名，不能改成 snake_case。"""
    result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                  'long_term').calculate(20000)
    for key in ('totalFee', 'reimburseBase', 'deductible', 'totalReimburse',
                'personalPay', 'reimburseRatio', 'finalRate'):
        assert key in result['summary']
    assert {'summary', 'comparison', 'recordInfo', 'tips', 'formula'} <= set(result)


def test_policy_year_can_be_pinned():
    """可以指定政策版本，结果随之变化。"""
    older = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                 'long_term', policy_year=2025).calculate(50000)
    newer = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                 'long_term', policy_year=2026).calculate(50000)
    assert older['policyYear'] == 2025
    assert newer['policyYear'] == 2026
    assert older['summary']['basicReimburse'] < newer['summary']['basicReimburse']


# --------------------------------------------------------------------------
# 材料清单
# --------------------------------------------------------------------------
def test_checklist_route_uses_city_names():
    checklist = generate_checklist('wuhan', 'shanghai')
    assert checklist['route'] == '武汉市 → 上海市'


def test_checklist_inpatient_materials():
    checklist = generate_checklist('wuhan', 'shanghai', 'inpatient')
    texts = [item['text'] for item in checklist['requiredItems']]
    assert any('出院小结' in t for t in texts)
    assert any('身份证' in t for t in texts)


def test_checklist_outpatient_materials_differ():
    inpatient = generate_checklist('wuhan', 'shanghai', 'inpatient')
    outpatient = generate_checklist('wuhan', 'shanghai', 'outpatient')
    assert {i['text'] for i in inpatient['requiredItems']} != {
        i['text'] for i in outpatient['requiredItems']}
    assert any('门诊病历' in i['text'] for i in outpatient['requiredItems'])


def test_checklist_emergency_materials():
    checklist = generate_checklist('wuhan', 'shanghai', 'emergency')
    assert any('急诊' in i['text'] for i in checklist['requiredItems'])


def test_checklist_has_steps_and_channels():
    checklist = generate_checklist('wuhan', 'shanghai')
    assert len(checklist['steps']) >= 5
    assert checklist['recordChannels']
    assert len(checklist['recordTypes']) == 4
