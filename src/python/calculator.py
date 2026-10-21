# -*- coding: utf-8 -*-
"""医保报销计算引擎（对外统一入口）。

本模块是计算引擎的门面，把 :mod:`mediclear` 包的能力集中导出，
保持与旧版完全一致的 API::

    from calculator import ReimbursementCalculator, quick_calculate, generate_explanation

    result = quick_calculate('beijing', 'employee', 10000)
    print(result['summary']['total_reimburse'])
    print(result['explanation'])

再加上新增能力::

    from calculator import (
        calculate_chronic,        # 门诊慢特病
        calculate_contribution,   # 医保缴费
        calculate_personal_account,  # 个人账户每月返多少钱
        calculate_maternity,      # 生育保险 / 生育津贴
        compare_cities,           # 多城市对比
        export_json,              # 导出 JSON
        generate_detail_sheet,    # 生成发票式明细单
        explain_why_less,         # 为什么报销比预期少
        suggest_improvements,     # 怎么才能多报一点
    )

完整的架构说明见同目录下的 ``README_python.md``。
"""

from mediclear.constants import (  # noqa: F401  (对外暴露枚举)
    AUDIENCE_ELDERLY,
    AUDIENCE_GENERAL,
    AUDIENCE_PROFESSIONAL,
    EMPLOYEE,
    INPATIENT,
    OUTPATIENT,
    RESIDENT,
)
from mediclear.data import PolicyRepository, get_repository  # noqa: F401
from mediclear.engine import (  # noqa: F401
    ENGINE_VERSION,
    ReimbursementCalculator,
    compare_cities,
    quick_calculate,
)
from mediclear.exceptions import (  # noqa: F401
    InsufficientDataError,
    InvalidInputError,
    MediClearError,
    PolicyDataError,
    PolicyNotFoundError,
    PolicyValidationError,
    VersionNotFoundError,
)
from mediclear.explain import (  # noqa: F401
    explain_why_less,
    generate_explanation,
    generate_explanation_sections,
    suggest_improvements,
)
from mediclear.export import (  # noqa: F401
    format_comparison,
    generate_detail_sheet,
    to_json,
    to_json_file,
)
from mediclear.models import FeeItem  # noqa: F401
from mediclear.policies import (  # noqa: F401
    BasicMedicalInsurance,
    ChronicDiseasePolicy,
    ContributionPolicy,
    HouseholdMutualAid,
    MaternityInsurance,
    PersonalAccountPolicy,
    PolicyModule,
    SeriousIllnessInsurance,
    list_modules as list_policy_modules,
)

__all__ = [
    # 核心（旧API）
    'ReimbursementCalculator',
    'quick_calculate',
    'generate_explanation',
    # 各险种便捷函数
    'calculate_chronic',
    'calculate_contribution',
    'calculate_personal_account',
    'calculate_maternity',
    # 解释增强
    'generate_explanation_sections',
    'explain_why_less',
    'suggest_improvements',
    # 导出
    'to_json',
    'to_json_file',
    'export_json',
    'generate_detail_sheet',
    'format_comparison',
    # 对比
    'compare_cities',
    # 险种模块
    'BasicMedicalInsurance',
    'SeriousIllnessInsurance',
    'ChronicDiseasePolicy',
    'PersonalAccountPolicy',
    'ContributionPolicy',
    'MaternityInsurance',
    'HouseholdMutualAid',
    'PolicyModule',
    'list_policy_modules',
    # 数据层
    'PolicyRepository',
    'get_repository',
    # 模型与常量
    'FeeItem',
    'EMPLOYEE',
    'RESIDENT',
    'OUTPATIENT',
    'INPATIENT',
    'AUDIENCE_GENERAL',
    'AUDIENCE_ELDERLY',
    'AUDIENCE_PROFESSIONAL',
    # 异常
    'MediClearError',
    'PolicyNotFoundError',
    'PolicyDataError',
    'PolicyValidationError',
    'VersionNotFoundError',
    'InvalidInputError',
    'InsufficientDataError',
    # 版本
    'ENGINE_VERSION',
]


# --------------------------------------------------------------------------
# 各险种的便捷函数
# --------------------------------------------------------------------------
def _fee_items_from_total(total_fee, class_a_ratio=0.6, class_b_ratio=0.3,
                          self_pay_ratio=0.1):
    """把总费用按比例拆成甲类 / 乙类 / 自费的明细列表。"""
    return [
        {'name': '甲类费用', 'amount': total_fee * class_a_ratio,
         'category': 'class_a', 'item_type': 'other'},
        {'name': '乙类费用', 'amount': total_fee * class_b_ratio,
         'category': 'class_b', 'item_type': 'other'},
        {'name': '自费费用', 'amount': total_fee * self_pay_ratio,
         'category': 'self_pay', 'item_type': 'other'},
    ]


def calculate_chronic(city_code, disease_code, total_fee, insurance_type='employee',
                      certified=True, class_a_ratio=0.6, class_b_ratio=0.3,
                      self_pay_ratio=0.1, year_accumulated=0, policy_year=None):
    """计算门诊慢特病报销。

    Args:
        city_code: 城市代码
        disease_code: 病种代码，如 ``'hypertension'``（高血压）、``'diabetes'``（糖尿病）
        total_fee: 本次门诊总费用
        insurance_type: 险种
        certified: 是否已办理该病种的慢特病认定备案
        class_a_ratio / class_b_ratio / self_pay_ratio: 费用构成占比
        year_accumulated: 本年度该病种已累计报销金额（用于判断年度限额）
        policy_year: 政策版本年份

    Returns:
        dict: 计算结果，``summary`` / ``chronic_details`` / ``formula`` 等
    """
    calc = ReimbursementCalculator(city_code, insurance_type, 'outpatient',
                                   year_accumulated=year_accumulated,
                                   policy_year=policy_year)
    result = calc.calculate(_fee_items_from_total(
        total_fee, class_a_ratio, class_b_ratio, self_pay_ratio),
        chronic_disease=disease_code, certified=certified)

    if not result.get('eligible', True):
        result['explanation'] = result.get('message', '')
    else:
        result['explanation'] = generate_explanation(result)
    return result


def calculate_contribution(city_code, insurance_type='employee', base_salary=None,
                           employment='employed', with_account=True, policy_year=None):
    """计算医保缴费（每月交多少钱、单位与个人各承担多少）。

    Args:
        city_code: 城市代码
        insurance_type: ``'employee'``（职工）或 ``'resident'``（居民）
        base_salary: 缴费基数（元/月）；职工医保不传则按当地下限估算
        employment: ``'employed'``（在职）/ ``'flexible'``（灵活就业）
        with_account: 灵活就业人员是否建立个人账户
        policy_year: 政策版本年份

    Returns:
        dict: 含 ``personal_monthly`` / ``employer_monthly`` / ``total_monthly``
        等键，附 ``formula`` 说明
    """
    bundle = get_repository().load(policy_year)
    module = ContributionPolicy(bundle)
    return module.monthly(city_code, insurance_type, base_salary=base_salary,
                          employment=employment, with_account=with_account)


def calculate_personal_account(city_code, base_salary=None, insurance_type='employee',
                               retired=False, months=12, opening_balance=0.0,
                               monthly_spend=0.0, policy_year=None):
    """计算职工医保个人账户每月返多少钱、一年能攒多少。

    Args:
        city_code: 城市代码
        base_salary: 缴费基数（在职人员必填，否则按当地下限估算）
        insurance_type: 险种（居民医保无个人账户）
        retired: 是否退休人员（按城市定额划入）
        months: 测算月数
        opening_balance: 期初余额
        monthly_spend: 每月从账户支出的金额
        policy_year: 政策版本年份

    Returns:
        dict: 含 ``monthly_credit`` / ``annual_credit`` / ``closing_balance`` 等
    """
    bundle = get_repository().load(policy_year)
    module = PersonalAccountPolicy(bundle)
    return module.project(city_code, insurance_type, base_salary=base_salary,
                          months=months, retired=retired,
                          opening_balance=opening_balance, monthly_spend=monthly_spend)


def calculate_maternity(city_code, fees=None, monthly_avg_wage=None,
                        delivery_type='normal_delivery', babies=1,
                        include_local_leave=True, personal_monthly_wage=None,
                        policy_year=None):
    """计算生育医疗费用报销与生育津贴。

    Args:
        city_code: 城市代码
        fees: 生育医疗花费，可以是总额（需配 ``delivery_type``）、
            ``{'normal_delivery': 3000}`` 或明细列表
        monthly_avg_wage: 用人单位上年度职工月平均工资（算津贴用）
        delivery_type: ``'normal_delivery'`` / ``'difficult_delivery'`` / ``'cesarean'``
        babies: 婴儿数量
        include_local_leave: 津贴是否计入地方奖励假
        personal_monthly_wage: 本人产假前月工资（用于计算单位补差）
        policy_year: 政策版本年份

    Returns:
        dict: 含 ``medical``（医疗费）与 ``allowance``（津贴）两部分
    """
    bundle = get_repository().load(policy_year)
    module = MaternityInsurance(bundle)
    return module.calculate(city_code, fees=fees, monthly_avg_wage=monthly_avg_wage,
                            delivery_type=delivery_type, babies=babies,
                            include_local_leave=include_local_leave,
                            personal_monthly_wage=personal_monthly_wage)


def export_json(result, indent=2):
    """把计算结果导出为 JSON 字符串（``to_json`` 的别名）。"""
    return to_json(result, indent=indent)


# --------------------------------------------------------------------------
# 命令行演示
# --------------------------------------------------------------------------
if __name__ == '__main__':
    # 示例：北京职工医保，住院，三级医院，总费用 10000 元
    demo = quick_calculate(
        city_code='beijing',
        insurance_type='employee',
        total_fee=10000,
        visit_type='inpatient',
        hospital_level='三级医院',
    )

    print(generate_detail_sheet(demo))
    print()
    print(demo['explanation'])
