# -*- coding: utf-8 -*-
"""集成测试与性能测试：模拟真实就医场景，串起数据层 → 计算 → 解释 → 导出。

这些用例刻意走**完整链路**而不是单个函数，因为需求里最容易出问题的
就是"单测全过、连起来全错"。
"""

import json
import time

import pytest

from calculator import (
    ReimbursementCalculator,
    calculate_chronic,
    compare_cities,
    generate_detail_sheet,
    generate_explanation,
    quick_calculate,
    to_json,
)
from mediclear.advanced.comparison import ComparisonAnalyzer
from mediclear.advanced.multi_admission import MultiAdmissionCalculator
from mediclear.advanced.optimizer import AnnualStrategyOptimizer
from mediclear.advanced.year_state import YearState
from mediclear.constants import CLASS_A, CLASS_B, SELF_PAY
from mediclear.data import get_repository
from mediclear.export import format_comparison
from relocation import RelocationCalculator


# --------------------------------------------------------------------------
# 场景一：一次普通住院的完整链路
# --------------------------------------------------------------------------
def test_scenario_single_admission_end_to_end():
    """从费用明细到可打印明细单，每一步的金额必须自洽。"""
    fee_items = [
        {'name': '阿莫西林胶囊', 'amount': 800, 'item_type': '药品'},
        {'name': 'CT 检查', 'amount': 1200, 'item_type': '检查'},
        {'name': '静脉输液', 'amount': 600, 'item_type': '治疗'},
        {'name': '进口支架', 'amount': 15000, 'category': SELF_PAY, 'item_type': '耗材'},
        {'name': '床位费', 'amount': 900, 'item_type': '床位'},
    ]

    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院')
    result = calc.calculate(fee_items)
    summary = result['summary']

    # 1) 金额自洽
    assert summary['total_fee'] == sum(item['amount'] for item in fee_items)
    assert summary['total_reimburse'] + summary['personal_pay'] == summary['total_fee']
    assert summary['basic_reimburse'] + summary['serious_reimburse'] == \
        summary['total_reimburse']

    # 2) 明细逐项可追溯
    assert len(result['items_detail']) == len(fee_items)
    assert sum(d['reimburse_base'] for d in result['items_detail']) == \
        summary['reimburse_base']

    # 3) 解释与明细单都能生成，且含关键数字
    explanation = generate_explanation(result)
    sheet = generate_detail_sheet(result)
    assert '报销' in explanation
    assert str(int(summary['total_reimburse'])) in sheet

    # 4) 导出 JSON 可再解析
    assert json.loads(to_json(result))['summary']['total_fee'] == summary['total_fee']


# --------------------------------------------------------------------------
# 场景二：高血压患者全年门诊
# --------------------------------------------------------------------------
def test_scenario_hypertension_full_year():
    """认定慢特病后，全年多次拿药的累计报销不应超过年度限额。"""
    quota = get_repository().current.resolve_chronic_disease(
        'hypertension', 'beijing')['annual_quota']

    total_reimburse = 0.0
    for _ in range(12):
        result = calculate_chronic('beijing', 'hypertension', 800,
                                   year_accumulated=total_reimburse)
        assert result['eligible'] is True
        total_reimburse += result['summary']['total_reimburse']

    assert total_reimburse <= quota + 0.01


def test_scenario_hypertension_unregistered_vs_registered():
    """办没办认定，报销差距要能算出来——这是产品最有说服力的数字。

    没办认定的不是"一分不报"，而是退回**普通门诊**政策结算，
    所以只是报得少，不会归零。
    """
    registered = calculate_chronic('beijing', 'hypertension', 20000)
    unregistered = calculate_chronic('beijing', 'hypertension', 20000, certified=False)

    assert registered['eligible'] is True
    assert unregistered['eligible'] is False
    assert unregistered['blocked_reason'] == 'not_certified'
    assert (registered['summary']['total_reimburse']
            > unregistered['summary']['total_reimburse'] > 0)


# --------------------------------------------------------------------------
# 场景三：同一年三次住院
# --------------------------------------------------------------------------
def test_scenario_three_admissions_in_one_year():
    """年内多次住院：起付线递减 + 大病保险按累计自付触发。"""
    bundle = get_repository().current
    visits = [
        {'amount': 30000, 'description': '第一次住院'},
        {'amount': 25000, 'description': '第二次住院'},
        {'amount': 60000, 'description': '第三次住院'},
    ]
    result = MultiAdmissionCalculator(bundle).simulate(
        visits, city_code='beijing', year=2026)

    state = result['state']
    assert state['total_fee'] == 115000.0
    assert state['admission_count'] == 3
    # 起付线 1300 + 650 + 0
    assert state['deductible_paid'] == 1950.0
    # 第三次金额大、且已免起付线，报销额应显著高于前两次
    reimburse = [v['total_reimburse'] for v in result['visits']]
    assert reimburse[2] > reimburse[1]

    # 全链条金额自洽
    assert state['total_reimburse'] + state['total_personal_pay'] == state['total_fee']


# --------------------------------------------------------------------------
# 场景四：年底要不要再看一次病
# --------------------------------------------------------------------------
def test_scenario_year_end_decision():
    """已经住过一次院的人，年底再住院应被建议"今年做"。"""
    bundle = get_repository().current
    state = YearState(year=2026, total_fee=40000, total_reimburse=28000,
                      total_personal_pay=12000, deductible_paid=1300, admission_count=1)
    optimizer = AnnualStrategyOptimizer(bundle)
    advice = optimizer.advise_timing('beijing', 'employee', 20000, year_state=state)
    assert advice['recommendation'] == 'this_year'
    assert abs(advice['difference']) > 0


# --------------------------------------------------------------------------
# 场景五：异地就医
# --------------------------------------------------------------------------
def test_scenario_relocation_records_reduce_the_loss():
    """从"没备案"改成"长期驻外备案"，能挽回多少损失。

    ``comparison.difference`` 指的是**基本医保**这一层的差额（本地 vs 本次异地），
    大病保险因为个人自付不同会各自计算，所以不能直接拿总额相减。
    """
    without = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                   'none').calculate(60000)
    with_record = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                       'long_term').calculate(60000)

    basic_recovered = (with_record['summary']['basicReimburse']
                       - without['summary']['basicReimburse'])
    assert basic_recovered > 0
    assert basic_recovered == pytest.approx(without['comparison']['difference'], abs=0.01)
    assert (with_record['summary']['totalReimburse']
            > without['summary']['totalReimburse'])


def test_scenario_relocation_never_beats_local():
    """异地就医不会比本地报得多（备案只是把比例还原到本地水平）。"""
    items = [{'name': '费用', 'amount': 40000, 'category': CLASS_A}]
    local = ReimbursementCalculator('wuhan', 'employee', 'inpatient', '三级医院').calculate(items)
    for record in ('long_term', 'temp', 'transfer', 'none'):
        result = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient',
                                      record).calculate(0, fee_items=items)
        assert result['summary']['basicReimburse'] <= local['summary']['basic_reimburse']


# --------------------------------------------------------------------------
# 场景六：家庭共济 + 慢特病组合
# --------------------------------------------------------------------------
def test_scenario_chronic_plus_household_aid():
    """老人门诊自付部分用子女的账户余额共济。"""
    from mediclear.policies.household import HouseholdMutualAid

    chronic = calculate_chronic('beijing', 'diabetes', 3000)
    self_pay = chronic['summary']['personal_pay']
    assert self_pay > 0

    aid = HouseholdMutualAid(get_repository().current).calculate(self_pay, 5000,
                                                                 member_name='父亲')
    assert aid['payable'] == self_pay
    assert aid['member_out_of_pocket'] == 0.0


# --------------------------------------------------------------------------
# 场景七：多城市 / 多方案对比（前端可直接渲染）
# --------------------------------------------------------------------------
def test_scenario_comparison_renderable_by_exporter():
    """对比分析器的输出必须能被导出器渲染成表格。"""
    bundle = get_repository().current
    analyzer = ComparisonAnalyzer(bundle)
    for comparison in (analyzer.compare_cities(['beijing', 'shanghai', 'wuhan'], 80000),
                       analyzer.compare_hospital_levels('beijing', 80000),
                       analyzer.compare_insurance_types('beijing', 80000),
                       analyzer.compare_chronic_certification('beijing', 'diabetes', 8000),
                       analyzer.compare_relocation('wuhan', 'shanghai', 80000)):
        text = format_comparison(comparison)
        assert text
        # 表格必须有分隔线，且每行都要有内容
        assert '─' * 10 in text
        body = [l for l in text.splitlines()[4:-2] if l.strip()]
        assert body


def test_scenario_engine_compare_cities_also_renderable():
    """引擎级 compare_cities 的返回也要能渲染（早期没有 label 字段会渲染成全 '-'）。"""
    text = format_comparison(compare_cities(['beijing', 'wuhan'], 'employee', 50000))
    assert '北京市' in text
    assert '武汉市' in text


# --------------------------------------------------------------------------
# 场景八：跨政策版本
# --------------------------------------------------------------------------
def test_scenario_version_switch_changes_every_layer():
    """切到 2025 年政策后，报销额、对比、异地都要跟着变。"""
    repository = get_repository()
    try:
        result_2026 = quick_calculate('beijing', 'employee', 50000, 'inpatient', '三级医院')
        repository.use_version(2025)
        result_2025 = quick_calculate('beijing', 'employee', 50000, 'inpatient', '三级医院')
        assert (result_2025['summary']['total_reimburse']
                < result_2026['summary']['total_reimburse'])
        assert result_2025['policy']['policy_year'] == 2025
        assert result_2025['meta']['policy_version'] == '2025.1'

        relocation = RelocationCalculator('wuhan', 'shanghai', policy_year=2025).calculate(30000)
        assert relocation['policyYear'] == 2025
    finally:
        repository.use_version(2026)


# --------------------------------------------------------------------------
# 性能测试
# --------------------------------------------------------------------------
def test_performance_single_calculation():
    """单次计算应在毫秒级——政策数据已缓存，不该反复读盘。"""
    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院')
    items = [{'name': '费用%d' % i, 'amount': 100, 'category': CLASS_A} for i in range(50)]

    start = time.perf_counter()
    for _ in range(200):
        calc.calculate(items)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, '200 次计算耗时 %.3fs，过慢' % elapsed


def test_performance_repository_is_cached():
    """重复取同一版本必须命中缓存，而不是重新解析 JSON。"""
    repository = get_repository()
    start = time.perf_counter()
    for _ in range(500):
        repository.load(2026)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, '500 次取版本耗时 %.3fs，说明没有缓存' % elapsed


def test_performance_multi_admission_simulation():
    bundle = get_repository().current
    visits = [{'amount': 20000} for _ in range(12)]
    start = time.perf_counter()
    MultiAdmissionCalculator(bundle).simulate(visits, city_code='beijing', year=2026)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, '12 次住院模拟耗时 %.3fs，过慢' % elapsed


def test_performance_city_comparison_scales():
    """对比 19 个城市应在可接受的交互时间内完成。"""
    cities = [c['code'] for c in get_repository().current.list_cities()]
    start = time.perf_counter()
    comparison = compare_cities(cities, 'employee', 50000)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0
    assert len(comparison['rows']) == len(cities)


def test_performance_explanation_and_export():
    result = quick_calculate('beijing', 'employee', 150000, 'inpatient', '三级医院')
    start = time.perf_counter()
    for _ in range(100):
        generate_explanation(result, 'elderly')
        generate_detail_sheet(result)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0
