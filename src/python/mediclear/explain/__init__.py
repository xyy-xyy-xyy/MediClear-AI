# -*- coding: utf-8 -*-
"""结果解释层：把冰冷的数字翻译成用户看得懂的话。"""

from .generator import (
    ExplanationBuilder,
    explain_why_less,
    generate_explanation,
    generate_explanation_sections,
    suggest_improvements,
)

__all__ = [
    'generate_explanation',
    'generate_explanation_sections',
    'explain_why_less',
    'suggest_improvements',
    'ExplanationBuilder',
]
