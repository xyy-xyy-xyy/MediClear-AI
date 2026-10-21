# -*- coding: utf-8 -*-
"""引擎级常量定义。

本模块只描述"系统里有哪些概念"（枚举、别名、排序），
不描述"数值是多少"——所有政策数值都在 ``data/policies/<年份>/*.json``。

这样拆分的目的是：新增城市/险种时只改 JSON，新增概念时才改本文件。
"""

# --------------------------------------------------------------------------
# 医保类型
# --------------------------------------------------------------------------
EMPLOYEE = 'employee'
RESIDENT = 'resident'

#: 医保类型代码集合，用于入参校验
INSURANCE_TYPE_CODES = (EMPLOYEE, RESIDENT)

# --------------------------------------------------------------------------
# 就医类型
# --------------------------------------------------------------------------
OUTPATIENT = 'outpatient'
INPATIENT = 'inpatient'
EMERGENCY = 'emergency'

VISIT_TYPE_CODES = (OUTPATIENT, INPATIENT, EMERGENCY)

# --------------------------------------------------------------------------
# 费用报销类别（甲类 / 乙类 / 自费）
# --------------------------------------------------------------------------
CLASS_A = 'class_a'
CLASS_B = 'class_b'
SELF_PAY = 'self_pay'

#: 报销类别代码集合
FEE_CLASS_CODES = (CLASS_A, CLASS_B, SELF_PAY)

#: 报销类别中文名（与政策 JSON 中的 name 保持一致，作为兜底）
FEE_CLASS_NAMES = {
    CLASS_A: '甲类',
    CLASS_B: '乙类',
    SELF_PAY: '自费',
}

# --------------------------------------------------------------------------
# 费用项目类型（药品 / 检查 / 治疗 / 耗材 ...）
#
# 与"报销类别"是正交的两个维度：
#   - item_type 回答"这是什么费用"（药品？检查？耗材？）
#   - category  回答"这类费用怎么报"（甲类全额？乙类先自付？自费？）
# 未显式给出 category 时，按 default_class 推断。
# --------------------------------------------------------------------------
ITEM_TYPES = {
    'drug': {'name': '药品', 'default_class': CLASS_B},
    'exam': {'name': '检查检验', 'default_class': CLASS_B},
    'treatment': {'name': '治疗', 'default_class': CLASS_B},
    'consumable': {'name': '医用耗材', 'default_class': CLASS_B},
    'surgery': {'name': '手术', 'default_class': CLASS_B},
    'blood': {'name': '血液制品', 'default_class': CLASS_B},
    'bed': {'name': '床位', 'default_class': CLASS_A},
    'service': {'name': '诊查服务', 'default_class': CLASS_A},
    'nursing': {'name': '护理', 'default_class': CLASS_A},
    'other': {'name': '其他', 'default_class': CLASS_A},
}

#: 默认费用项目类型
DEFAULT_ITEM_TYPE = 'other'

#: 中文（及常见写法）→ 项目类型代码，便于用户直接填中文
ITEM_TYPE_ALIASES = {
    '药品': 'drug', '西药': 'drug', '西药费': 'drug', '中成药': 'drug', '中药': 'drug',
    '检查': 'exam', '检验': 'exam', '检查费': 'exam', '检查检验': 'exam', '化验': 'exam',
    '治疗': 'treatment', '治疗费': 'treatment', '理疗': 'treatment',
    '耗材': 'consumable', '医用耗材': 'consumable', '材料': 'consumable', '材料费': 'consumable',
    '手术': 'surgery', '手术费': 'surgery',
    '血液': 'blood', '输血': 'blood', '血费': 'blood',
    '床位': 'bed', '床位费': 'bed',
    '诊查': 'service', '诊查费': 'service', '挂号': 'service', '挂号费': 'service',
    '护理': 'nursing', '护理费': 'nursing',
    '其他': 'other', '其它': 'other',
}

# --------------------------------------------------------------------------
# 医院等级
# --------------------------------------------------------------------------
#: 医院等级由低到高（等级越低，报销比例通常越高）
HOSPITAL_LEVEL_ORDER = ('一级医院', '二级医院', '三级医院')

DEFAULT_HOSPITAL_LEVEL = '三级医院'

# --------------------------------------------------------------------------
# 解释受众（决定 generate_explanation 的语气与详略）
# --------------------------------------------------------------------------
AUDIENCE_GENERAL = 'general'
AUDIENCE_ELDERLY = 'elderly'
AUDIENCE_PROFESSIONAL = 'professional'

AUDIENCES = {
    AUDIENCE_GENERAL: {
        'name': '普通人',
        'description': '默认风格：通俗、简洁、口语化',
    },
    AUDIENCE_ELDERLY: {
        'name': '长辈',
        'description': '大字提示、句子更短、避免专业术语、重点突出',
    },
    AUDIENCE_PROFESSIONAL: {
        'name': '专业人士',
        'description': '保留公式、分段明细、政策口径与计算过程',
    },
}

# --------------------------------------------------------------------------
# 异地就医备案类型
# --------------------------------------------------------------------------
RECORD_LONG_TERM = 'long_term'
RECORD_TEMP = 'temp'
RECORD_TRANSFER = 'transfer'
RECORD_NONE = 'none'

RECORD_TYPE_CODES = (RECORD_LONG_TERM, RECORD_TEMP, RECORD_TRANSFER, RECORD_NONE)

# --------------------------------------------------------------------------
# 门诊慢特病等级
# --------------------------------------------------------------------------
CHRONIC_LEVEL_COMMON = '慢性病'
CHRONIC_LEVEL_SPECIAL = '特殊疾病'

# --------------------------------------------------------------------------
# 数据边界
# --------------------------------------------------------------------------
#: 金额保留小数位
MONEY_PRECISION = 2

#: 报销比例保留小数位（百分数形式）
RATIO_PRECISION = 1
