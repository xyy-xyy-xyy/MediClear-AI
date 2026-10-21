# -*- coding: utf-8 -*-
"""医算清 MediClear-AI 医保计算引擎。

包结构与职责::

    mediclear/
    ├── constants.py       引擎级枚举（险种、就医类型、费用类型...）
    ├── exceptions.py      统一异常体系
    ├── models.py          输入模型（FeeItem）
    ├── data/              政策数据层：JSON 加载 / 校验 / 版本管理
    ├── policies/          险种计算模块（基本医保、大病、慢特病、生育...）
    ├── advanced/          高级计算（多次住院、策略推荐、方案对比）
    ├── explain/           AI 风格解释生成
    ├── export/            结果导出（JSON、明细单）
    └── engine.py          对外门面 ReimbursementCalculator

向后兼容入口仍在 ``src/python/`` 顶层的 ``calculator.py`` / ``rules.py`` /
``relocation.py``，它们只是对本包的薄封装。
"""

__version__ = '2.0.0'
__all__ = ['__version__']
