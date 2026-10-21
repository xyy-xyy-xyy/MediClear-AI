# -*- coding: utf-8 -*-
"""快速测试新增功能"""
import sys
sys.path.insert(0, '.')

from relocation import RelocationCalculator, generate_checklist
from rules import get_supported_cities

# 测试1：异地就医计算
print('测试1：异地就医报销计算（武汉->上海，已备案）')
calc = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient', 'long_term')
result = calc.calculate(20000)
s = result['summary']
print(f'  总费用: {s["totalFee"]} 元')
print(f'  基准比例: {s["baseRate"]}%')
print(f'  调整后比例: {s["finalRate"]}%')
print(f'  报销金额: {s["totalReimburse"]} 元')
print(f'  个人自付: {s["personalPay"]} 元')
print(f'  报销比例: {s["reimburseRatio"]}%')
print()

# 测试2：未备案对比
print('测试2：异地就医（未备案）对比')
calc2 = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient', 'none')
result2 = calc2.calculate(20000)
s2 = result2['summary']
print(f'  未备案比例: {s2["finalRate"]}%')
print(f'  未备案报销: {s2["totalReimburse"]} 元')
diff = s["totalReimburse"] - s2["totalReimburse"]
print(f'  少报: {diff} 元')
print()

# 测试3：材料清单
print('测试3：材料清单生成')
checklist = generate_checklist('beijing', 'shanghai', 'inpatient', 'work')
print(f'  路线: {checklist["route"]}')
print(f'  必备材料: {len(checklist["requiredItems"])} 项')
print(f'  补充材料: {len(checklist["optionalItems"])} 项')
print(f'  办理步骤: {len(checklist["steps"])} 步')
print()

# 测试4：城市列表
print('测试4：支持城市列表')
cities = get_supported_cities()
print(f'  支持城市总数: {len(cities)} 个')
for c in cities:
    print(f'    - {c["name"]} ({c["tier"]})')
print()

# 测试5：快速验证所有城市都能计算
print('测试5：所有城市快速验证')
failed = []
for city in cities:
    try:
        from calculator import quick_calculate
        r = quick_calculate(city['code'], 'employee', 10000, 'inpatient')
        print(f'  {c["name"]}: OK (报销{r["summary"]["total_reimburse"]}元)')
    except Exception as e:
        failed.append(city['name'])
        print(f'  {c["name"]}: FAIL - {e}')

print()
if failed:
    print(f'❌ 失败城市: {failed}')
else:
    print('✅ 所有城市计算正常!')
