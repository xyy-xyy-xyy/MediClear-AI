# 医算清 · Python 计算引擎

医保报销计算引擎，为「医算清 MediClear-AI」提供全部医保测算能力：
基本医保、大病保险、门诊慢特病、个人账户、医保缴费、生育保险、家庭共济、
异地就医、年度最优策略，以及解释生成与导出。

> **数据说明**：仓库内的政策数据为**演示数据**，用于功能验证，
> 非官方口径。实际报销以当地医保部门核定为准。任何对外输出都必须保留
> 免责声明（引擎已内置）。

---

## 1. 快速开始

```python
from calculator import quick_calculate, generate_explanation

result = quick_calculate('beijing', 'employee', 10000, 'inpatient', '三级医院')

result['summary']['total_reimburse']   # 6035.0
result['summary']['reimburse_ratio']   # 60.35
print(generate_explanation(result))    # 含"计算过程 / 为什么比预期少 / 怎么多报"
```

命令行直接跑一个完整演示（含发票式明细单）：

```bash
python calculator.py
python relocation.py     # 异地就医演示
```

运行测试：

```bash
python -m pytest -q                    # 全部用例
python -m pytest test_calc.py -q       # 仅最早的 10 个向后兼容用例
```

---

## 2. 目录结构

```
src/python/
├── calculator.py         ← 对外统一入口（门面 + 新增便捷函数）
├── rules.py              ← 政策查询兼容层（老代码 from rules import ... 仍可用）
├── relocation.py         ← 异地就医（保持 camelCase 返回结构）
├── mediclear/            ← 引擎实现
│   ├── constants.py      枚举常量（险种 / 就医类型 / 费用类别 / 受众 / 项目类型）
│   ├── exceptions.py     异常层次
│   ├── models.py         FeeItem 费用明细模型与归一化
│   ├── engine.py         计算编排 + 多城市对比
│   ├── data/             政策数据层
│   │   ├── policies/     
│   │   │   ├── index.json          版本清单
│   │   │   ├── 2026/{base,cities,extended}.json
│   │   │   └── 2025/{base,cities,extended}.json
│   │   ├── repository.py PolicyRepository / PolicyBundle
│   │   └── schema.py     数据校验
│   ├── policies/         各险种计算模块
│   ├── advanced/         年度累计 / 多次住院 / 最优策略 / 方案对比
│   ├── explain/          AI 解释生成
│   └── export/           JSON 导出 / 明细单 / 对比表
└── test_*.py             191 个测试
```

**分层依赖是单向的**：`calculator.py` → `engine` → `policies` → `data`。
`data` 层不依赖任何计算逻辑，因此**换政策只需要改 JSON**。

---

## 3. 核心概念

### 3.1 报销的计算顺序

引擎严格按下面的顺序编排，每一步的输出都是下一步的输入：

```
费用明细（FeeItem）
  │  按类别折算：甲类 ×100%，乙类 ×80%，自费 ×0%
  ▼
报销基数 reimburse_base
  │  减去起付线
  ▼
可报销部分 above_deductible
  │  按医院等级取分段表，超额累进
  ▼
基本医保报销额 basic_reimburse        ← 受年度封顶线限制
  │  总费用 − 基本报销 = 个人自付
  ▼
大病保险报销额 serious_reimburse      ← 按年度累计自付累进
  │
  ▼
最终报销额 / 个人自付
```

### 3.2 超额累进（分段报销）

医疗保险的分段是**超额累进**，不是"按最高档一刀切"。
北京职工住院三级医院：0~3 万报 85%，3~4 万报 90%，4 万以上报 95%。
可报销 5 万时：

```
30000 × 85% + 10000 × 90% + 10000 × 95% = 44000 元
```

这段逻辑封装在 `TierTable`（`mediclear/policies/base.py`），
`breakdown()` 会返回每一段的金额、比例、算式，用于生成"钱是怎么算出来的"。

### 3.3 费用类型（药品 / 检查 / 治疗 / 耗材……）

`FeeItem` 支持 10 种项目类型（`mediclear/constants.py` 的 `ITEM_TYPES`），
每种类型有**默认的报销类别**，因此调用方可以只写类型不写类别：

| 项目类型 | 默认报销类别 | | 项目类型 | 默认报销类别 |
|----------|--------------|---|----------|--------------|
| 药品 `drug` | 乙类 | | 血液制品 `blood` | 乙类 |
| 检查检验 `exam` | 乙类 | | 床位 `bed` | 甲类 |
| 治疗 `treatment` | 乙类 | | 诊查服务 `service` | 甲类 |
| 医用耗材 `consumable` | 乙类 | | 护理 `nursing` | 甲类 |
| 手术 `surgery` | 乙类 | | 其他 `other` | 甲类 |

```python
{'name': '阿莫西林', 'amount': 120, 'item_type': '药品'}   # → 乙类（先自付一部分）
{'name': '床位费',   'amount': 300, 'item_type': '床位'}   # → 甲类（全额计入报销基数）
```

也支持中文别名（`药品`/`西药`/`耗材`/`检查费`……），以及只给数量单价让引擎自己算：

```python
{'name': 'CT', 'quantity': 2, 'unit_price': 300, 'item_type': '检查'}  # amount = 600
```

---

## 4. 政策数据层

### 4.1 数据文件

| 文件 | 内容 |
|------|------|
| `index.json` | 版本清单（年份、版本号、状态） |
| `base.json` | 险种、就医类型、费用类别、医院等级、大病保险、异地就医 |
| `cities.json` | 14 个城市的基本医保政策（起付线 / 封顶线 / 分段比例） |
| `extended.json` | 慢特病、个人账户、缴费、生育、家庭共济、起付线递减 |

### 4.2 多版本管理

```python
from mediclear.data import get_repository

repo = get_repository()
repo.list_versions()          # [{year: 2026, version: '2026.1', ...}, {year: 2025, ...}]
bundle_2025 = repo.use_version(2025)   # 全局切换
bundle_2026 = repo.load(2026)          # 只取不切换

from calculator import ReimbursementCalculator
ReimbursementCalculator('beijing', 'employee', policy_year=2025)  # 单次指定
```

**版本是隔离的**：每个 `PolicyBundle` 自带年份，计算、对比、异地就医
都从自己那份 bundle 读数据，不会串味。需要改政策试算时用深拷贝：

```python
from mediclear.data import clone_bundle

bundle = clone_bundle(repo.current)   # 独立副本，版本号带 '-clone' 后缀
```

`clone_bundle()` 是 `mediclear.data` 的模块级函数（不是仓库的方法），
副本改完不影响全局缓存。

### 4.3 数据校验

新增或修改政策 JSON 后，先跑校验：

```python
from mediclear.data import get_repository
report = get_repository().validate(2026)
# {'ok': True, 'errors': [], 'warnings': []}
```

`schema.py` 会检查：

- 必备字段（险种、费用类别、城市政策结构）
- **分段表必须从 0 开始且首尾相接**（否则一段费用会凭空消失）
- 报销比例在 `[0, 1]` 之间
- 起付线不得高于封顶线
- 跨文件引用（慢特病 / 缴费基数 / 生育是否覆盖了所有城市）

也可以校验一份内存中的新数据：

```python
from mediclear.data.schema import validate_policy_bundle, assert_valid
report = validate_policy_bundle(base, cities, extended, source='新数据')
assert_valid(report)   # 有 error 时抛 PolicyValidationError（带 .errors 列表）
```

### 4.4 新增一个城市

**只改 JSON，不用改代码**：在 `cities.json` 里加一个城市条目，
在 `extended.json` 里补上缴费基数和慢特病覆盖，然后跑校验。

### 4.5 新增一个险种

1. 在 `mediclear/policies/` 下新建模块，继承 `PolicyModule`；
2. 实现 `calculate()`；
3. 用 `@register_module` 注册。

`list_modules()` 会自动发现它。基类强制实现 `calculate()`，
并在构造时校验参数类型——注册了却没实现 `calculate()` 的模块会直接实例化失败，
不会留到运行时才炸。

---

## 5. 各险种用法

### 5.1 基本医保 + 大病保险（默认串联）

```python
from calculator import ReimbursementCalculator

calc = ReimbursementCalculator('beijing', 'employee', 'inpatient', '三级医院',
                               year_accumulated=0)      # 当年已累计报销
result = calc.calculate([
    {'name': '阿莫西林', 'amount': 800,  'item_type': '药品'},
    {'name': 'CT',       'amount': 1200, 'item_type': '检查'},
    {'name': '进口支架', 'amount': 15000, 'category': 'self_pay'},
], apply_serious_illness=True)
```

`result` 的主要字段：

| 键 | 说明 |
|----|------|
| `summary` | 总额、报销基数、起付线、基本/大病报销额、个人自付、报销比例 |
| `category_stats` | 甲类 / 乙类 / 自费的金额与笔数 |
| `item_type_stats` | 按药品 / 检查 / 治疗……分组的金额 |
| `items_detail` | 逐项明细：金额、类别、纳入报销基数、自付部分 |
| `basic_details` | 基本医保细节：分段明细、封顶线、剩余额度 |
| `serious_illness` | 大病保险：起付线、分段、封顶 |
| `formula` | 完整的公式步骤与文字说明 |
| `policy` / `meta` | 本次使用的政策版本与医院等级（含回退信息） |
| `warnings` | 数据或参数上的提醒 |
| `explanation` | （`quick_calculate` 附带）成段的解释文本 |

### 5.2 门诊慢特病

支持 13 个病种，其中高血压、糖尿病在所有 14 个城市都有独立口径。

```python
from calculator import calculate_chronic

result = calculate_chronic('beijing', 'hypertension', 800,
                           certified=True, year_accumulated=0)
result['chronic_details']['annual_quota']    # 年度限额
result['chronic_details']['remaining_quota'] # 剩余额度
result['chronic_details']['capped_by_quota'] # 是否已被限额截断
```

未办理认定时 `eligible=False`，`blocked_reason='not_certified'`，
并退回**普通门诊**政策结算（不是一分不报），`message` 会说明原因。

### 5.3 个人账户

```python
from calculator import calculate_personal_account

result = calculate_personal_account('beijing', base_salary=10000, months=12,
                                    opening_balance=0, monthly_spend=500)
result['monthly_credit']      # 280.0（基数 × (个人 2% + 单位划入 0.8%)）
result['closing_balance']     # 年末余额
result['insufficient_month']  # 余额在第几个月不够花（None 表示够）
```

缴费基数会自动按当地上下限收敛（低于下限按下限，高于上限按上限），
`detail.base_clamped` 会标出是否被收敛。退休人员按城市定额划入；
居民医保不设个人账户，`monthly_credit` 为 0。

### 5.4 医保缴费

```python
from calculator import calculate_contribution

result = calculate_contribution('beijing', 'employee', base_salary=10000)
result['personal_monthly']        # 200.0（2%）
result['employer_medical_monthly']# 800.0（8%）
result['maternity_monthly']       # 80.0（0.8%，由单位缴）
result['total_monthly']           # 1080.0
```

灵活就业人员自己承担全部费用，且有两种档位——**建不建个人账户**：

```python
calculate_contribution('beijing', 'employee', base_salary=10000,
                       employment='flexible', with_account=True)
# 总费率 = 统筹 8% + 账户 2% = 10%，其中 2% 划入个人账户
calculate_contribution('beijing', 'employee', base_salary=10000,
                       employment='flexible', with_account=False)
# 只缴统筹 8%，医保卡里没有钱
```

### 5.5 生育保险

```python
from calculator import calculate_maternity

result = calculate_maternity('beijing', fees=3000, monthly_avg_wage=10000,
                             delivery_type='normal_delivery', babies=1,
                             personal_monthly_wage=12000)
result['medical']['summary']['total_reimburse']  # 生育医疗费报销
result['allowance']['allowance']                 # 生育津贴
result['allowance']['makeup_by_employer']        # 单位应补的工资差额
```

津贴天数：顺产 98 天，剖宫产按"难产"口径计发，多胞胎每多一胎加天数，
再加地方奖励假（北京 30 天）——`include_local_leave=False` 可关掉。
本人工资高于单位平均工资时，差额由单位补足。

### 5.6 家庭共济

```python
from mediclear.policies.household import HouseholdMutualAid
from mediclear.data import get_repository

aid = HouseholdMutualAid(get_repository().current)
aid.calculate(2000, 5000)      # 成员自付 2000，账户余额 5000 → 全付，余额剩 3000

# 余额不够时在多个成员间分配
aid.allocate(3000, [{'name': '父亲', 'self_pay': 3000},
                    {'name': '孩子', 'self_pay': 1000}],
             strategy='proportional')   # 按需求比例：2250 / 750
aid.allocate(3000, [...], strategy='priority')  # 按顺序：3000 / 0（先给老人）
```

### 5.7 异地就医

异地就医的规则一句话概括：**比例按参保地走，再按备案类型加减。**

| 备案类型 | 比例调整 |
|----------|----------|
| `long_term` 长期驻外 | 不变 |
| `temp` 临时外出 | −5% |
| `transfer` 转诊转院 | −8% |
| `none` 未备案 | −20% |

```python
from relocation import RelocationCalculator, generate_checklist

calc = RelocationCalculator('wuhan', 'shanghai', 'employee', 'inpatient', 'long_term')
result = calc.calculate(20000)
result['summary']['basicReimburse']        # 与本地相同
result['comparison']['difference']         # 与"本地就医"的差额（0）
result['formula']['text']                  # 完整的计算过程

generate_checklist('wuhan', 'shanghai', 'inpatient', reason='work')
# 必备材料 / 补充材料 / 办理步骤 / 备案渠道
```

返回结构保持 **camelCase**（前端契约），并可指定 `policy_year`。
`comparison.difference` 是**基本医保**一层的差额，不含大病保险。

---

## 6. 高级计算

### 6.1 年度累计状态

```python
from mediclear.advanced import YearState, VisitRecord

state = YearState(year=2026)
state.add_visit(VisitRecord(index=1, visit_type='inpatient', total_fee=20000,
                            reimburse_base=15800, deductible=1300,
                            reimburse=11972, serious_reimburse=0, personal_pay=8028))
state.remaining_quota(300000)   # 剩余封顶额度
state.to_dict() / YearState.from_dict(...)   # 可持久化，跨会话恢复
```

### 6.2 多次住院（起付线递减）

同一年度内多次住院，起付线按次数递减（默认 1.0 / 0.5 / 0），
且大病保险按**累计个人自付**增量计算：

```python
from mediclear.advanced import MultiAdmissionCalculator
from mediclear.data import get_repository

module = MultiAdmissionCalculator(get_repository().current)
result = module.simulate(
    [{'amount': 20000}, {'amount': 20000}, {'amount': 30000}],
    city_code='beijing', year=2026, opening_state=None)   # opening_state 支持年中接入

result['state']                              # 年度累计
result['visits']                             # 逐次明细（含每次的起付线说明）
result['savings_from_decreasing_deductible'] # 递减规则省下的钱

# 与"不递减"对比，量化这项政策的收益
module.compare_with_no_decrease([...], city_code='beijing', year=2026)
```

### 6.3 年度最优策略

回答"年底要不要再看一次病"——这是用户最常问、也最容易算错的问题。

```python
from mediclear.advanced import AnnualStrategyOptimizer

optimizer = AnnualStrategyOptimizer(get_repository().current)

# 对比四种安排方式，给出推荐
optimizer.recommend('beijing', 'employee', 30000, year_state=state)
# → options（now_once / split_year / cross_year / lower_level）
#   recommended, max_saving, advice

# 直接回答"今年做还是明年做"
optimizer.advise_timing('beijing', 'employee', 30000, year_state=state)
# → recommendation: 'this_year' / 'next_year' / 'either'
#   difference（为负表示今年更省）, reasons, disclaimer
```

已经交过起付线时，答案是**今年做**：不用重复承担起付线，
而且第二次的起付线还递减。

### 6.4 方案对比

```python
from mediclear.advanced import ComparisonAnalyzer

analyzer = ComparisonAnalyzer(get_repository().current)

analyzer.compare_cities(['beijing', 'shanghai', 'wuhan'], 50000)
analyzer.compare_hospital_levels('beijing', 50000)
analyzer.compare_insurance_types('beijing', 30000)
analyzer.compare_chronic_certification('beijing', 'hypertension', 5000)  # 办不办认定
analyzer.compare_relocation('wuhan', 'shanghai', 30000)                  # 四种备案
analyzer.compare_scenarios([...], sort_by='total_reimburse')             # 任意自定义
```

每类对比都返回统一结构：`rows`（含 `label`）、`best`、`worst`、`spread`、
`summary`（一句结论）、`baseline`。**结果可以直接交给导出器渲染成表格。**

---

## 7. 解释生成

```python
from calculator import generate_explanation, generate_explanation_sections
from calculator import explain_why_less, suggest_improvements

generate_explanation(result)                        # 普通人（默认）
generate_explanation(result, 'elderly')             # 老人
generate_explanation(result, 'professional')        # 专业人士
generate_explanation_sections(result, 'elderly')    # 结构化，供前端分段渲染
```

### 7.1 三种受众

| 受众 | 特点 |
|------|------|
| `general` | 默认。带 emoji、结构清晰、点到要点 |
| `elderly` | 大白话。先讲结论再讲原因，不出现术语；确有必要提到"起付线"这类名词时，会紧跟着用一句白话解释（`要先扣掉 1300 元（这叫起付线）`） |
| `professional` | 直接给计算口径、公式步骤、政策版本号 |

「为什么报得比想得少」和「怎么才能多报」两段**同样按受众改写**：
每条原因和建议都带一份 `plain` 大白话版本，老人版自动取用。
（这两段早期版本只有专业口径，老人版会把术语直接甩给用户。）

### 7.2 为什么报销比预期少

`explain_why_less(result)` 返回按**严重程度 + 金额**排序的原因列表，
覆盖 7 类：自费项目、乙类先自付、起付线、医院等级、分段档位、
年度封顶、大病保险未到门槛。

```python
[{'key': 'deductible',
  'reason': '起付线以内的钱要自己先掏',
  'detail': '政策规定起付线 1300 元，这部分不参与报销。本次从报销基数中扣除了 1300 元。',
  'amount': 1300.0,
  'severity': 'high',
  'plain': {'reason': '有一笔钱要先自己掏，过了这个数才开始报',
            'detail': '医保规定先自己付 1300 元，这 1300 元不报，超过的部分才按比例报。'}}]
```

### 7.3 怎么才能多报一点

`suggest_improvements(result)` 返回 7 类可操作建议，每条带
`action` / `detail` / `estimated_gain` / `confidence`
（`estimate` 为有金额估算，`qualitative` 为定性建议）/ `plain`。

---

## 8. 输出与导出

```python
from calculator import to_json, generate_detail_sheet, export_json
from mediclear.export import to_json_file, format_comparison

to_json(result)                       # JSON 字符串（默认不转义中文）
to_json_file(result, 'result.json')   # 写文件（UTF-8）
generate_detail_sheet(result)         # 发票式"报销计算明细单"
format_comparison(comparison)         # 对比结果 → 等宽表格
```

- **明细单**带表头、逐项费用、计算过程、温馨提示，中英文混排按
  `unicodedata.east_asian_width` 计算显示宽度对齐（不用 `len()`）。
  明细单编号由结果内容哈希生成，因此**同一笔费用每次生成的结果完全一致**。
- **对比表**同时兼容 `ComparisonAnalyzer` 和 `engine.compare_cities` 的返回
  （后者没有 `label` 字段，方案名自动退回城市名），`columns` 既接受
  `(键, 表头, 宽度, 对齐)` 四元组，也接受直接写列名的简写。

---

## 9. 扩展指南

### 新增一个城市

改 `cities.json` + `extended.json`，跑 `validate()`。不需要动代码。

### 新增一个险种模块

```python
from mediclear.policies.base import PolicyModule, register_module

@register_module
class LongTermCareInsurance(PolicyModule):
    code = 'ltc'
    name = '长期护理保险'

    def calculate(self, city_code, **kwargs):
        ...
```

`PolicyModule` 会强制校验 `bundle` 类型并要求实现 `calculate()`。

### 修改计算规则

规则的入口集中在 `TierTable`（分段）与各 `policies/*.py` 模块。
如果只是**数据变了**（比例、起付线、限额），改成 JSON 就够了；
只有**算法本身变了**才需要动代码。

---

## 10. 测试

191 个用例，覆盖单元 / 集成 / 性能：

| 文件 | 范围 |
|------|------|
| `test_calc.py` | 最早的 10 个向后兼容用例（**不要改**） |
| `test_mediclear_core.py` | 分段表、费用模型、基本医保、大病保险、异常层次 |
| `test_mediclear_data.py` | 版本管理、缓存、数据校验、`rules.py` 兼容层 |
| `test_mediclear_policies_ext.py` | 慢特病、个人账户、缴费、生育、家庭共济 |
| `test_mediclear_advanced.py` | 年度累计、多次住院、最优策略、方案对比 |
| `test_mediclear_explain_export.py` | 三种受众解释、导出、明细单对齐 |
| `test_mediclear_relocation.py` | 备案调整、本地对比、材料清单 |
| `test_mediclear_integration.py` | 8 个真实场景 + 5 个性能用例 |

性能用例给了宽松的阈值（如 200 次计算 < 2 秒），目的是**发现缓存失效、
复杂度退化这类量级问题**，不是做精确基准测试。

```bash
python -m pytest -q                      # 全部
python -m pytest test_mediclear_integration.py -q
```

---

## 11. 向后兼容

老代码无需任何改动：

```python
from calculator import ReimbursementCalculator, quick_calculate, generate_explanation
from rules import get_supported_cities, get_city_policy, CITY_POLICIES
from relocation import RelocationCalculator, generate_checklist
```

保证事项：

- `quick_calculate` / `ReimbursementCalculator` 的**签名与返回结构不变**，
  新增能力都是**附加键**。
- `PolicyNotFoundError` / `InvalidInputError` 同时继承 `ValueError`，
  老的 `except ValueError` 继续生效。
- `rules.py` 的模块级常量仍然可用。注意它们绑定的是**默认年份**的数据，
  调 `set_policy_year()` 后会被重新绑定——所以请通过
  `rules.CITY_POLICIES` 动态访问，而不是在导入时就 `from rules import CITY_POLICIES`。
- `relocation.py` 的 `summary` 保持 camelCase。

## 12. 已知边界

- 政策数据为演示数据，**不是官方口径**，不能直接作为报销依据。
- 大病保险按年度累计，引擎用 `year_accumulated` / `opening_state`
  表达"今年已经报过多少"；跨年度的真实累计需要调用方自己维护状态。
- 异地就医的 `comparison.difference` 只比较基本医保一层。
- 慢特病未认定时退回普通门诊结算，与"完全不予报销"是两回事，
  `blocked_reason` 会明确标注。
