# -*- coding: utf-8 -*-
"""险种计算模块。

每个模块负责一类待遇的计算，统一继承 :class:`~mediclear.policies.base.PolicyModule`，
并通过 ``@register_module`` 登记到注册表，便于新增险种时被自动发现。

============================  ================================  ==========================
模块                          代码                              解决的问题
============================  ================================  ==========================
基本医疗保险                  ``basic``                         住院/门诊能报多少
大病保险                      ``serious_illness``               自付超过起付线后再报一次
门诊慢特病                    ``chronic``                       高血压/糖尿病等门诊专项待遇
个人账户                      ``personal_account``              医保卡每月返多少钱
医保缴费                      ``contribution``                  每月交多少钱、单位与个人各多少
生育保险                      ``maternity``                     生育医疗费与生育津贴
家庭共济                      ``household``                     个人账户余额给家人用
============================  ================================  ==========================
"""

from .accounts import PersonalAccountPolicy
from .base import (
    PolicyModule,
    TierTable,
    get_module,
    list_modules,
    register_module,
    resolve_tiers,
    round_money,
    round_ratio,
)
from .basic import BasicMedicalInsurance
from .chronic import ChronicDiseasePolicy
from .contribution import ContributionPolicy
from .household import HouseholdMutualAid
from .maternity import MaternityInsurance
from .serious_illness import SeriousIllnessInsurance

__all__ = [
    'PolicyModule',
    'TierTable',
    'register_module',
    'get_module',
    'list_modules',
    'resolve_tiers',
    'round_money',
    'round_ratio',
    'BasicMedicalInsurance',
    'SeriousIllnessInsurance',
    'ChronicDiseasePolicy',
    'PersonalAccountPolicy',
    'ContributionPolicy',
    'MaternityInsurance',
    'HouseholdMutualAid',
]
