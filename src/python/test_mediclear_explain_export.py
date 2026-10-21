# -*- coding: utf-8 -*-
"""AI 解释与输出导出单元测试。"""

import json

import pytest

from calculator import (
    generate_explanation,
    generate_explanation_sections,
    quick_calculate,
    to_json,
)
from mediclear.constants import AUDIENCE_ELDERLY, AUDIENCE_GENERAL, AUDIENCE_PROFESSIONAL
from mediclear.explain.generator import ExplanationBuilder, explain_why_less, suggest_improvements
from mediclear.export import (
    display_width,
    format_comparison,
    generate_detail_sheet,
    pad,
    to_json_file,
)


@pytest.fixture
def result():
    """北京职工住院三级医院，1 万元，含自费项目——足以触发多条解释。"""
    return quick_calculate('beijing', 'employee', 10000, 'inpatient', '三级医院')


@pytest.fixture
def big_result():
    """15 万元的大额费用，触发大病保险与封顶线相关解释。"""
    return quick_calculate('beijing', 'employee', 150000, 'inpatient', '三级医院')


# --------------------------------------------------------------------------
# 解释生成
# --------------------------------------------------------------------------
def test_explanation_is_substantial(result):
    text = generate_explanation(result)
    assert len(text) > 100
    for keyword in ('总费用', '报销', '温馨提示'):
        assert keyword in text


def test_explanation_appears_in_quick_calculate(result):
    assert result['explanation']
    assert '报销' in result['explanation']


def test_explanation_sections_have_expected_keys(result):
    sections = generate_explanation_sections(result)
    assert {'headline', 'how_it_works', 'why_less', 'improvements', 'tips', 'audience'} <= set(sections)
    assert sections['headline']
    assert isinstance(sections['how_it_works'], list)


def test_three_audiences_produce_different_text(result):
    """普通人 / 老人 / 专业人士 三种口吻必须真的不一样。"""
    general = generate_explanation(result, AUDIENCE_GENERAL)
    elderly = generate_explanation(result, AUDIENCE_ELDERLY)
    professional = generate_explanation(result, AUDIENCE_PROFESSIONAL)
    assert general != elderly
    assert general != professional
    assert elderly != professional


def test_elderly_audience_explains_in_plain_language(result):
    """老人版通篇用大白话：术语只在第一次出现时用括号解释，不裸用。"""
    sections = generate_explanation_sections(result, AUDIENCE_ELDERLY)
    why_less = '\n'.join(
        line for block in sections['sections'] if block['key'] == 'why_less'
        for line in block['lines'])
    assert '起付线' not in why_less
    assert '自己' in why_less or '自己掏' in why_less

    elderly = generate_explanation(result, AUDIENCE_ELDERLY)
    # 允许"（这叫起付线）"这种顺带解释，不允许直接拿术语当句子主干
    assert elderly.count('起付线') <= 1


def test_plain_language_variants_attached_to_reasons(result):
    """每条原因都带一份大白话说法，供老人版渲染。"""
    for reason in explain_why_less(result):
        assert reason['plain']['reason']
        assert reason['plain']['detail']
        assert reason['plain']['reason'] != reason['reason']


def test_plain_language_variants_attached_to_suggestions(result):
    for suggestion in suggest_improvements(result):
        assert suggestion['plain']['action']
        assert suggestion['plain']['detail']


def test_professional_audience_includes_policy_terms(result):
    professional = generate_explanation(result, AUDIENCE_PROFESSIONAL)
    assert '起付线' in professional or '统筹' in professional


def test_explanation_falls_back_to_general_for_unknown_audience(result):
    assert generate_explanation(result, 'klingon') == generate_explanation(result, AUDIENCE_GENERAL)


def test_explanation_builder_exposes_each_section(result):
    builder = ExplanationBuilder(result)
    assert builder.headline()
    assert builder.how_it_works()
    assert isinstance(builder.tips(), list)
    assert builder.build()['headline'] == builder.headline()


# --------------------------------------------------------------------------
# 为什么报销比预期少
# --------------------------------------------------------------------------
def test_why_less_identifies_deductible(result):
    reasons = explain_why_less(result)
    keys = {r['key'] for r in reasons}
    assert 'deductible' in keys
    assert 'self_pay_items' in keys


def test_why_less_reasons_are_sorted_by_severity_then_amount(result):
    reasons = explain_why_less(result)
    order = {'high': 0, 'medium': 1, 'low': 2}
    ranked = [(order[r['severity']], -r['amount']) for r in reasons]
    assert ranked == sorted(ranked)


def test_why_less_every_reason_has_an_amount_and_detail(result):
    for reason in explain_why_less(result):
        assert reason['amount'] >= 0
        assert reason['detail']
        assert reason['reason']


def test_zero_fee_result_has_no_why_less_reasons():
    empty = quick_calculate('beijing', 'employee', 0)
    assert explain_why_less(empty) == []


def test_big_bill_still_reports_cost_drivers(big_result):
    """大额账单的原因里仍然要指出最大的几项扣减。"""
    reasons = explain_why_less(big_result)
    assert reasons
    assert {'self_pay_items', 'class_b_deduct'} <= {r['key'] for r in reasons}


def test_serious_illness_reason_appears_below_the_threshold():
    """个人自付没到大病保险起付线时，应提示"还差多少"。"""
    small = quick_calculate('beijing', 'employee', 2000, 'outpatient', '三级医院')
    keys = {r['key'] for r in explain_why_less(small)}
    assert 'serious_illness' in keys


def test_quota_reason_appears_when_capped():
    """封顶后必须给出"额度用完"的解释。"""
    from mediclear.engine import ReimbursementCalculator
    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院',
                                   year_accumulated=299000)
    capped = calc.calculate([{'name': '费用', 'amount': 100000, 'category': 'class_a'}])
    keys = {r['key'] for r in explain_why_less(capped)}
    assert 'quota' in keys


# --------------------------------------------------------------------------
# 怎么才能多报一点
# --------------------------------------------------------------------------
def test_improvements_cover_actions_for_a_typical_bill(result):
    keys = {s['key'] for s in suggest_improvements(result)}
    assert {'replace_self_pay', 'lower_level_hospital', 'chronic_certification',
            'household_mutual_aid'} <= keys


def test_improvements_add_deductible_advice_when_nothing_was_reimbursed():
    """费用没超过起付线时，要提示"年度内继续就医可累计"。"""
    small = quick_calculate('beijing', 'employee', 500, 'outpatient', '三级医院')
    keys = {s['key'] for s in suggest_improvements(small)}
    assert 'accumulate_to_deductible' in keys


def test_improvements_suggest_lower_level_hospital(result):
    keys = {s['key'] for s in suggest_improvements(result)}
    assert 'lower_level_hospital' in keys


def test_improvements_have_actionable_text(result):
    for suggestion in suggest_improvements(result):
        assert suggestion['action']
        assert suggestion['detail']
        assert suggestion['confidence'] in ('estimate', 'qualitative')
        gain = suggestion['estimated_gain']
        assert gain is None or gain >= 0


def test_improvements_mention_chronic_certification_for_chronic_visit():
    """慢特病未认定时，建议里必须出现"办理认定"。"""
    blocked = quick_calculate('beijing', 'employee', 5000, 'outpatient')
    blocked['eligible'] = False
    blocked['chronic_details'] = {'disease_code': 'hypertension', 'certified': False}
    keys = {s['key'] for s in suggest_improvements(blocked)}
    assert 'chronic_certification' in keys


# --------------------------------------------------------------------------
# JSON 导出
# --------------------------------------------------------------------------
def test_to_json_produces_parseable_json(result):
    payload = to_json(result)
    assert isinstance(payload, str)
    parsed = json.loads(payload)
    assert parsed['summary']['total_reimburse'] == result['summary']['total_reimburse']


def test_to_json_keeps_chinese_readable_by_default(result):
    payload = to_json(result)
    assert '总费用' in payload
    assert '\\u' not in payload


def test_to_json_ascii_escape_option(result):
    payload = to_json(result, ensure_ascii=True)
    assert json.loads(payload)['summary']['total_fee'] == result['summary']['total_fee']
    assert '\\u' in payload


def test_to_json_file_writes_utf8(tmp_path, result):
    path = str(tmp_path / 'result.json')
    to_json_file(result, path)
    with open(path, encoding='utf-8') as handle:
        assert json.load(handle)['summary']['total_fee'] == 10000.0


# --------------------------------------------------------------------------
# 报销计算明细单
# --------------------------------------------------------------------------
def test_detail_sheet_contains_core_numbers(result):
    sheet = generate_detail_sheet(result)
    assert '报销' in sheet
    assert '10000' in sheet
    assert '6035' in sheet


def test_detail_sheet_has_formula_section(result):
    sheet = generate_detail_sheet(result, include_formula=True)
    assert '计算过程' in sheet or '公式' in sheet


def test_detail_sheet_can_omit_items(result):
    with_items = generate_detail_sheet(result, include_items=True)
    without_items = generate_detail_sheet(result, include_items=False)
    assert len(with_items) > len(without_items)


def test_detail_sheet_columns_align_for_cjk(result):
    """中英文混排时按显示宽度对齐，不能靠 len() 计算。"""
    lines = [line for line in generate_detail_sheet(result).splitlines() if line.strip()]
    assert len(lines) > 5
    assert display_width('中文') == 4
    assert display_width('ab') == 2
    assert display_width('中a') == 3
    assert pad('中', 4) == '中  '


def test_detail_sheet_is_deterministic(result):
    assert generate_detail_sheet(result) == generate_detail_sheet(result)


# --------------------------------------------------------------------------
# 对比表导出
# --------------------------------------------------------------------------
def test_format_comparison_renders_rows():
    from mediclear.engine import compare_cities
    comparison = compare_cities(['beijing', 'shanghai'], 'employee', 50000)
    text = format_comparison(comparison, title='城市对比')
    assert '城市对比' in text
    assert '北京市' in text and '上海市' in text


def test_format_comparison_handles_empty_rows():
    text = format_comparison({'rows': []})
    assert isinstance(text, str)


def test_format_comparison_accepts_custom_columns():
    from mediclear.engine import compare_cities
    comparison = compare_cities(['beijing', 'wuhan'], 'employee', 30000)
    text = format_comparison(comparison, columns=['city_name', 'total_reimburse'])
    assert '北京市' in text
