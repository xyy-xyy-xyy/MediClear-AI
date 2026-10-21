# -*- coding: utf-8 -*-
"""高级计算：年度视角下的累计、策略与对比。

单次报销算的是"这一笔能报多少"；本包回答的是"一整年怎么安排最划算"。

===========================  ==============================================
模块                          解决的问题
===========================  ==============================================
``year_state``               年度累计状态（起付线 / 封顶线 / 自付累计）
``multi_admission``          多次住院累计 + 起付线递减
``optimizer``                年度最优就医策略（年底要不要再看一次病）
``comparison``               城市 / 医院等级 / 险种 / 方案的横向对比
===========================  ==============================================
"""

from .comparison import ComparisonAnalyzer
from .multi_admission import MultiAdmissionCalculator
from .optimizer import AnnualStrategyOptimizer
from .year_state import VisitRecord, YearState

__all__ = [
    'YearState',
    'VisitRecord',
    'MultiAdmissionCalculator',
    'AnnualStrategyOptimizer',
    'ComparisonAnalyzer',
]
