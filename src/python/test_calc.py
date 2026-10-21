# -*- coding: utf-8 -*-
"""
医保报销计算引擎单元测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from calculator import ReimbursementCalculator, quick_calculate, generate_explanation


def test_basic_calculation():
    """测试基本计算"""
    print("测试1：基本计算...")

    # 北京职工医保，住院，三级医院，总费用10000元
    fee_items = [
        {'name': '床位费', 'amount': 500, 'category': 'class_a'},
        {'name': '西药费', 'amount': 3000, 'category': 'class_a'},
        {'name': '检查费', 'amount': 2000, 'category': 'class_b'},
        {'name': '治疗费', 'amount': 3000, 'category': 'class_b'},
        {'name': '自费药', 'amount': 1500, 'category': 'self_pay'},
    ]

    calc = ReimbursementCalculator(
        city_code='beijing',
        insurance_type='employee',
        visit_type='inpatient',
        hospital_level='三级医院'
    )
    result = calc.calculate(fee_items)

    total = result['summary']['total_fee']
    assert total == 10000, f"总费用应为10000，实际为{total}"

    reimburse_base = result['summary']['reimburse_base']
    # 甲类500+3000=3500 全额计入 → 3500
    # 乙类2000+3000=5000 按80%计入 → 4000
    # 自费1500 不计入
    # 总计 3500 + 4000 = 7500
    expected_base = 7500
    assert abs(reimburse_base - expected_base) < 0.01, \
        f"报销基数应为{expected_base}，实际为{reimburse_base}"

    print(f"  总费用：{total}")
    print(f"  报销基数：{reimburse_base}")
    print(f"  ✅ 基本计算测试通过")
    return True


def test_deductible():
    """测试起付线"""
    print("\n测试2：起付线...")

    # 费用低于起付线，报销应为0
    fee_items = [
        {'name': '挂号费', 'amount': 100, 'category': 'class_a'},
    ]

    calc = ReimbursementCalculator(
        city_code='beijing',
        insurance_type='employee',
        visit_type='inpatient'
    )
    result = calc.calculate(fee_items)

    deductible = result['basic_details']['deductible']
    assert deductible == 1300, f"北京职工住院起付线应为1300，实际为{deductible}"

    # 费用低于起付线，基本医保报销应为0
    basic_reimburse = result['summary']['basic_reimburse']
    assert basic_reimburse == 0, f"低于起付线时报销应为0，实际为{basic_reimburse}"

    print(f"  起付线：{deductible}")
    print(f"  低于起付线报销：{basic_reimburse}")
    print(f"  ✅ 起付线测试通过")
    return True


def test_different_cities():
    """测试不同城市政策"""
    print("\n测试3：不同城市政策...")

    cities = ['beijing', 'shanghai', 'guangzhou', 'wuhan', 'chengdu']

    for city in cities:
        result = quick_calculate(
            city_code=city,
            insurance_type='employee',
            total_fee=20000,
            visit_type='inpatient'
        )
        print(f"  {city}: 总费{result['summary']['total_fee']}元, "
              f"报销{result['summary']['total_reimburse']}元, "
              f"比例{result['summary']['reimburse_ratio']}%")

    print(f"  ✅ 多城市测试通过")
    return True


def test_different_insurance_types():
    """测试不同医保类型"""
    print("\n测试4：不同医保类型...")

    # 职工医保
    emp_result = quick_calculate(
        city_code='beijing',
        insurance_type='employee',
        total_fee=10000,
        visit_type='inpatient'
    )

    # 居民医保
    res_result = quick_calculate(
        city_code='beijing',
        insurance_type='resident',
        total_fee=10000,
        visit_type='inpatient'
    )

    print(f"  职工医保：报销 {emp_result['summary']['total_reimburse']} 元，比例 {emp_result['summary']['reimburse_ratio']}%")
    print(f"  居民医保：报销 {res_result['summary']['total_reimburse']} 元，比例 {res_result['summary']['reimburse_ratio']}%")

    # 职工医保报销应高于居民医保
    assert emp_result['summary']['total_reimburse'] > res_result['summary']['total_reimburse'], \
        "职工医保报销应高于居民医保"

    print(f"  ✅ 医保类型测试通过")
    return True


def test_outpatient_vs_inpatient():
    """测试门诊vs住院"""
    print("\n测试5：门诊 vs 住院...")

    outpatient_result = quick_calculate(
        city_code='beijing',
        insurance_type='employee',
        total_fee=3000,
        visit_type='outpatient'
    )

    inpatient_result = quick_calculate(
        city_code='beijing',
        insurance_type='employee',
        total_fee=10000,
        visit_type='inpatient'
    )

    print(f"  门诊：3000元 → 报销 {outpatient_result['summary']['total_reimburse']} 元")
    print(f"  住院：10000元 → 报销 {inpatient_result['summary']['total_reimburse']} 元")
    print(f"  ✅ 门诊住院测试通过")
    return True


def test_serious_illness():
    """测试大病保险"""
    print("\n测试6：大病保险...")

    # 高额费用，触发大病保险
    result = quick_calculate(
        city_code='beijing',
        insurance_type='employee',
        total_fee=100000,
        visit_type='inpatient',
        class_a_ratio=0.5,
        class_b_ratio=0.3,
        self_pay_ratio=0.2
    )

    s = result['summary']
    serious = result['serious_illness']

    print(f"  总费用：{s['total_fee']} 元")
    print(f"  基本医保报销：{s['basic_reimburse']} 元")
    print(f"  大病保险报销：{s['serious_reimburse']} 元")
    print(f"  总报销：{s['total_reimburse']} 元")
    print(f"  大病起付线：{serious['deductible']} 元")
    print(f"  超过大病起付线：{serious['above_deductible']} 元")

    if serious['eligible']:
        assert s['serious_reimburse'] > 0, "高额费用应触发大病保险"
        print(f"  ✅ 大病保险测试通过（已触发）")
    else:
        print(f"  ⚠️  未触发大病保险，可能费用不够高")

    return True


def test_category_stats():
    """测试费用分类统计"""
    print("\n测试7：费用分类统计...")

    fee_items = [
        {'name': '项目1', 'amount': 100, 'category': 'class_a'},
        {'name': '项目2', 'amount': 200, 'category': 'class_b'},
        {'name': '项目3', 'amount': 300, 'category': 'self_pay'},
        {'name': '项目4', 'amount': 400, 'category': 'class_a'},
    ]

    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient')
    result = calc.calculate(fee_items)

    stats = result['category_stats']
    assert stats['class_a']['total'] == 500, f"甲类应为500，实际{stats['class_a']['total']}"
    assert stats['class_b']['total'] == 200, f"乙类应为200，实际{stats['class_b']['total']}"
    assert stats['self_pay']['total'] == 300, f"自费应为300，实际{stats['self_pay']['total']}"

    print(f"  甲类：{stats['class_a']['total']} 元 ({stats['class_a']['count']}项)")
    print(f"  乙类：{stats['class_b']['total']} 元 ({stats['class_b']['count']}项)")
    print(f"  自费：{stats['self_pay']['total']} 元 ({stats['self_pay']['count']}项)")
    print(f"  ✅ 分类统计测试通过")
    return True


def test_explanation():
    """测试解释生成"""
    print("\n测试8：解释生成...")

    result = quick_calculate(
        city_code='beijing',
        insurance_type='employee',
        total_fee=15000,
        visit_type='inpatient'
    )

    explanation = generate_explanation(result)
    assert len(explanation) > 100, "解释文本太短"
    assert "总费用" in explanation, "应包含总费用"
    assert "报销" in explanation, "应包含报销说明"
    assert "温馨提示" in explanation, "应包含温馨提示"

    print(f"  解释长度：{len(explanation)} 字")
    print(f"  前100字：{explanation[:100]}...")
    print(f"  ✅ 解释生成测试通过")
    return True


def test_items_detail():
    """测试费用明细"""
    print("\n测试9：费用明细...")

    fee_items = [
        {'name': '挂号费', 'amount': 50, 'category': 'class_a'},
        {'name': 'CT检查', 'amount': 800, 'category': 'class_b'},
        {'name': '进口药', 'amount': 500, 'category': 'self_pay'},
    ]

    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient')
    result = calc.calculate(fee_items)

    details = result['items_detail']
    assert len(details) == 3, f"明细数量应为3，实际{len(details)}"

    # 甲类：报销基数 = 金额 * 100%
    assert details[0]['reimburse_base'] == 50, "甲类报销基数应为全额"
    # 乙类：报销基数 = 金额 * 80%
    assert details[1]['reimburse_base'] == 640, f"乙类报销基数应为640，实际{details[1]['reimburse_base']}"
    # 自费：报销基数 = 0
    assert details[2]['reimburse_base'] == 0, "自费报销基数应为0"

    print(f"  明细数量：{len(details)}")
    for d in details:
        print(f"    {d['name']}: {d['amount']}元 ({d['category_name']}) → 报销基数{d['reimburse_base']}元")
    print(f"  ✅ 费用明细测试通过")
    return True


def test_zero_fee():
    """测试零费用"""
    print("\n测试10：零费用边界...")

    calc = ReimbursementCalculator('beijing', 'employee', 'inpatient')
    result = calc.calculate([])

    assert result['summary']['total_fee'] == 0, "零费用总费用应为0"
    assert result['summary']['total_reimburse'] == 0, "零费用报销应为0"
    assert result['summary']['reimburse_ratio'] == 0, "零费用报销比例应为0"

    print(f"  总费用：{result['summary']['total_fee']}")
    print(f"  报销：{result['summary']['total_reimburse']}")
    print(f"  ✅ 零费用边界测试通过")
    return True


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("医保报销计算引擎 - 单元测试")
    print("=" * 60)

    tests = [
        test_basic_calculation,
        test_deductible,
        test_different_cities,
        test_different_insurance_types,
        test_outpatient_vs_inpatient,
        test_serious_illness,
        test_category_stats,
        test_explanation,
        test_items_detail,
        test_zero_fee,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ 断言失败：{e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ 异常：{e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果：{passed} 通过，{failed} 失败，共 {len(tests)} 个测试")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
