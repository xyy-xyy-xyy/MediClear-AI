# -*- coding: utf-8 -*-
"""医保政策规则数据库（兼容层）。

**政策数据现在存放在 JSON 里**，不再硬编码在本文件中::

    mediclear/data/policies/index.json     版本清单
    mediclear/data/policies/2026/base.json 险种 / 就医类型 / 费用分类 / 大病 / 异地就医
    mediclear/data/policies/2026/cities.json 各城市基本医保政策
    mediclear/data/policies/2026/extended.json 慢特病 / 个人账户 / 生育 / 缴费 / 共济

本模块保留原来的模块级常量与查询函数，方便老代码继续 ``from rules import ...``。
新增能力（版本切换、数据校验）请直接使用 :mod:`mediclear.data`。

.. note::
   模块级常量（``CITY_POLICIES`` 等）绑定的是**默认年份**的数据。
   调用 :func:`set_policy_year` 可以整体切到别的年份，切换后这些常量会被重新绑定，
   因此请通过 ``rules.CITY_POLICIES`` 动态访问，而不是在导入时就 ``from rules import CITY_POLICIES``。
"""

from mediclear.data import PolicyRepository, get_repository

# --------------------------------------------------------------------------
# 全局仓库与默认政策包
# --------------------------------------------------------------------------
repository: PolicyRepository = get_repository()
_bundle = repository.load()

#: 医保类型定义
INSURANCE_TYPES = _bundle.insurance_types

#: 就医类型定义
VISIT_TYPES = _bundle.visit_types

#: 费用报销类别（甲类 / 乙类 / 自费）及纳入比例
FEE_CATEGORIES = _bundle.fee_categories

#: 医院等级
HOSPITAL_LEVELS = _bundle.hospital_levels

#: 大病保险政策
SERIOUS_ILLNESS_INSURANCE = _bundle.serious_illness

#: 异地就医政策
RELOCATION_POLICY = _bundle.relocation

#: 各城市医保政策（默认年份）
CITY_POLICIES = _bundle.cities

#: 门诊慢特病政策
CHRONIC_DISEASES = _bundle.chronic_diseases

#: 个人账户计入规则
PERSONAL_ACCOUNT = _bundle.personal_account

#: 医保缴费规则
CONTRIBUTION = _bundle.contribution

#: 生育保险规则
MATERNITY = _bundle.maternity

#: 家庭共济规则
HOUSEHOLD = _bundle.household

#: 多次住院起付线递减规则
MULTI_ADMISSION = _bundle.multi_admission

#: 全国通用参考政策的城市代码（用户所在城市不在城市库中时使用）
NATIONAL_CITY_CODE = 'national'


def _refresh_globals() -> None:
    """把模块级常量重新绑定到当前默认版本的政策数据。"""
    global _bundle, INSURANCE_TYPES, VISIT_TYPES, FEE_CATEGORIES, HOSPITAL_LEVELS
    global SERIOUS_ILLNESS_INSURANCE, RELOCATION_POLICY, CITY_POLICIES
    global CHRONIC_DISEASES, PERSONAL_ACCOUNT, CONTRIBUTION, MATERNITY
    global HOUSEHOLD, MULTI_ADMISSION

    _bundle = repository.current
    INSURANCE_TYPES = _bundle.insurance_types
    VISIT_TYPES = _bundle.visit_types
    FEE_CATEGORIES = _bundle.fee_categories
    HOSPITAL_LEVELS = _bundle.hospital_levels
    SERIOUS_ILLNESS_INSURANCE = _bundle.serious_illness
    RELOCATION_POLICY = _bundle.relocation
    CITY_POLICIES = _bundle.cities
    CHRONIC_DISEASES = _bundle.chronic_diseases
    PERSONAL_ACCOUNT = _bundle.personal_account
    CONTRIBUTION = _bundle.contribution
    MATERNITY = _bundle.maternity
    HOUSEHOLD = _bundle.household
    MULTI_ADMISSION = _bundle.multi_admission


def set_policy_year(year: int) -> dict:
    """切换政策版本（例如从 2025 切到 2026），返回该版本摘要。

    Args:
        year: 目标年份

    Returns:
        dict: 版本摘要，含 ``year`` / ``version`` / ``city_count``
    """
    bundle = repository.use_version(year)
    _refresh_globals()
    return bundle.describe()


def list_policy_versions():
    """列出全部可用政策版本。"""
    return repository.list_versions()


def validate_policies(year=None) -> dict:
    """校验政策数据完整性，返回报告（不抛异常）。"""
    return repository.validate(year)


# --------------------------------------------------------------------------
# 查询函数（保持原有签名）
# --------------------------------------------------------------------------
def get_supported_cities():
    """获取支持的城市列表。

    Returns:
        list[dict]: 每项含 ``code`` / ``name`` / ``province`` / ``tier``
    """
    return repository.current.list_cities()


def get_cities_by_tier(tier):
    """按城市级别获取城市列表。

    Args:
        tier: 城市级别，如 ``'一线'`` / ``'新一线'`` / ``'二线'``
    """
    return repository.current.cities_by_tier(tier)


def get_city_policy(city_code, insurance_type):
    """获取指定城市、指定医保类型的政策。

    Args:
        city_code: 城市代码
        insurance_type: ``'employee'`` 或 ``'resident'``

    Returns:
        dict | None: 政策字典，不存在返回 ``None``
    """
    return repository.current.get_city_policy(city_code, insurance_type)


def get_visit_type_policy(city_code, insurance_type, visit_type):
    """获取指定就医类型的政策。

    Returns:
        dict | None: 政策字典，不存在返回 ``None``
    """
    return repository.current.get_visit_policy(city_code, insurance_type, visit_type)


def get_national_default_policy(insurance_type=None):
    """获取「全国通用」参考政策。

    城市库里除 19 个具体城市外，还内置了一份全国通用参考政策
    （``rules.NATIONAL_CITY_CODE``，即 ``'national'``）：

    ==========  ============  ============
    项目        职工医保      居民医保
    ==========  ============  ============
    门诊起付线   200 元        200 元
    住院起付线   1500 元       1500 元
    门诊报销比   70%           50%
    住院报销比   85%           60%
    门诊封顶     2 万元        2 万元
    住院封顶     20 万元       20 万元
    ==========  ============  ============

    大病保险沿用 :data:`SERIOUS_ILLNESS_INSURANCE`（起付 15000 元、
    报销 60%、封顶 30 万元）。

    这些都是参考值，各地差异很大，展示结果时必须带上
    :func:`get_city_disclaimer` 返回的声明。

    Args:
        insurance_type: ``'employee'`` / ``'resident'``，留空则返回整条政策

    Returns:
        dict | None: 政策字典，不存在返回 ``None``
    """
    city = repository.current.cities.get(NATIONAL_CITY_CODE)
    if not city:
        return None
    if insurance_type is None:
        return city
    return city.get(insurance_type)


def get_city_disclaimer(city_code):
    """获取某城市的政策参考声明（全国通用政策会返回「参考值，以当地医保局为准」）。

    Args:
        city_code: 城市代码

    Returns:
        str: 声明文案；该城市没有声明时返回空字符串
    """
    city = repository.current.cities.get(city_code) or {}
    return city.get('disclaimer', '')
