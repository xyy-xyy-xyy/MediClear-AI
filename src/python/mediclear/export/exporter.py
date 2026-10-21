# -*- coding: utf-8 -*-
"""结果导出：JSON、发票式明细单、对比表格。

三种输出面向三种场景：

* :func:`to_json` / :func:`to_json_file` —— 给前端或下游系统消费
* :func:`generate_detail_sheet` —— 给用户看的"明细单"，可直接打印/截图
* :func:`format_comparison` —— 把对比分析结果排成等宽表格
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    'to_json',
    'to_json_file',
    'generate_detail_sheet',
    'format_comparison',
    'display_width',
    'pad',
]

LINE = '─'
DOUBLE_LINE = '═'
SHEET_WIDTH = 62

INSURANCE_LABELS = {'employee': '职工医保', 'resident': '居民医保'}
VISIT_LABELS = {'outpatient': '门诊', 'inpatient': '住院', 'emergency': '急诊',
                'chronic': '门诊慢特病'}


# --------------------------------------------------------------------------
# 等宽排版工具（中文字符占两格）
# --------------------------------------------------------------------------
def display_width(text: str) -> int:
    """字符串在等宽终端下的显示宽度（CJK 全角字符算 2）。"""
    width = 0
    for char in str(text):
        width += 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
    return width


def pad(text: str, width: int, align: str = 'left') -> str:
    """按显示宽度补齐（不是按字符数补齐）。

    Args:
        text: 待补齐文本
        width: 目标显示宽度
        align: ``'left'`` / ``'right'`` / ``'center'``
    """
    text = str(text)
    space = max(0, width - display_width(text))
    if align == 'right':
        return ' ' * space + text
    if align == 'center':
        left = space // 2
        return ' ' * left + text + ' ' * (space - left)
    return text + ' ' * space


def _truncate(text: str, width: int) -> str:
    """按显示宽度截断，超出部分用省略号。"""
    text = str(text)
    if display_width(text) <= width:
        return text
    result = ''
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if used + char_width > width - 1:
            break
        result += char
        used += char_width
    return result + '…'


# --------------------------------------------------------------------------
# JSON 导出
# --------------------------------------------------------------------------
def to_json(result: Mapping[str, Any], *, indent: int = 2,
            ensure_ascii: bool = False) -> str:
    """把计算结果序列化为 JSON 字符串。

    Args:
        result: 计算结果
        indent: 缩进，``None`` 表示紧凑输出
        ensure_ascii: 是否转义非 ASCII（中文场景建议 ``False``）
    """
    return json.dumps(dict(result), ensure_ascii=ensure_ascii, indent=indent,
                      default=str, sort_keys=False)


def to_json_file(result: Mapping[str, Any], path: str, *,
                 indent: int = 2, ensure_ascii: bool = False) -> str:
    """把计算结果写入 JSON 文件，返回写入的绝对路径。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fp:
        fp.write(to_json(result, indent=indent, ensure_ascii=ensure_ascii))
        fp.write('\n')
    return os.path.abspath(path)


# --------------------------------------------------------------------------
# 明细单
# --------------------------------------------------------------------------
def _money(value: Any) -> str:
    """金额展示，固定两位小数。"""
    try:
        return '%.2f' % float(value)
    except (TypeError, ValueError):
        return str(value)


def _receipt_no(result: Mapping[str, Any]) -> str:
    """生成稳定的单据编号（同样输入永远得到同样编号，便于复现）。"""
    summary = result.get('summary', {}) or {}
    policy = result.get('policy', {}) or {}
    seed = '%s|%s|%s|%s' % (
        policy.get('city_code', ''),
        summary.get('total_fee', 0),
        summary.get('total_reimburse', 0),
        policy.get('policy_year', ''),
    )
    digest = hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8].upper()
    return 'MC-%s-%s' % (policy.get('policy_year', '0000'), digest)


def generate_detail_sheet(result: Mapping[str, Any], *,
                          title: str = '医算清 · 医保报销计算明细单',
                          include_formula: bool = True,
                          include_items: bool = True) -> str:
    """生成发票样式的纯文本明细单。

    Args:
        result: 计算结果
        title: 单据标题
        include_formula: 是否附上计算过程说明
        include_items: 是否逐条列出费用明细

    Returns:
        str: 可直接打印的等宽文本
    """
    summary = result.get('summary', {}) or {}
    details = result.get('basic_details', {}) or {}
    policy = result.get('policy', {}) or {}
    meta = result.get('meta', {}) or {}
    serious = result.get('serious_illness', {}) or {}
    items = result.get('items_detail', []) or []

    lines: List[str] = []
    lines.append(DOUBLE_LINE * SHEET_WIDTH)
    lines.append(pad(title, SHEET_WIDTH, 'center'))
    lines.append(DOUBLE_LINE * SHEET_WIDTH)

    city_name = policy.get('city_name') or result.get('city_name') or policy.get('city_code', '-')
    left = '单据编号：%s' % _receipt_no(result)
    right = '政策版本：%s' % meta.get('policy_version', '-')
    lines.append(pad(left, SHEET_WIDTH - display_width(right)) + right)
    lines.append('')

    info_rows = [
        ('参保城市', city_name),
        ('医保类型', INSURANCE_LABELS.get(policy.get('insurance_type', '-'),
                                          policy.get('insurance_type', '-'))),
        ('就医类型', VISIT_LABELS.get(policy.get('visit_type', '-'),
                                      policy.get('visit_type', '-'))),
        ('医院等级', details.get('hospital_level', '-')),
        ('政策年度', str(meta.get('policy_year', '-'))),
    ]
    for label, value in info_rows:
        lines.append('%s%s' % (pad(label + '：', 20), value))

    # -- 费用明细 ---------------------------------------------------------
    if include_items and items:
        lines.append(LINE * SHEET_WIDTH)
        lines.append('【费用明细】')
        header = '%s%s%s%s%s' % (
            pad('序号', 6), pad('项目名称', 20), pad('类型', 12),
            pad('金额', 12, 'right'), pad('计入基数', 12, 'right'))
        lines.append(header)
        lines.append(LINE * SHEET_WIDTH)
        for index, item in enumerate(items, start=1):
            lines.append('%s%s%s%s%s' % (
                pad(index, 6),
                pad(_truncate(item.get('name', '-'), 19), 20),
                pad(_truncate(item.get('item_type_name', '-'), 11), 12),
                pad(_money(item.get('amount', 0)), 12, 'right'),
                pad(_money(item.get('reimburse_base', 0)), 12, 'right'),
            ))

    # -- 报销计算 ---------------------------------------------------------
    lines.append(LINE * SHEET_WIDTH)
    lines.append('【报销计算】')
    rows = [
        ('医疗总费用', summary.get('total_fee', 0), ''),
        ('纳入报销基数', summary.get('reimburse_base', 0), ''),
        ('起付线', -float(summary.get('deductible', 0) or 0), '政策规定不予报销的部分'),
        ('超过起付线部分', summary.get('above_deductible', 0), ''),
        ('基本医保报销', summary.get('basic_reimburse', 0), ''),
    ]
    if float(summary.get('serious_reimburse', 0) or 0) > 0:
        rows.append(('大病保险报销', summary.get('serious_reimburse', 0),
                     '个人自付超 %s 元后的二次报销' % _money(serious.get('deductible', 0))))

    for label, value, note in rows:
        amount = float(value or 0)
        text = ('-￥%s' % _money(abs(amount))) if amount < 0 else ('￥%s' % _money(amount))
        line = '%s%s' % (pad(label + '：', 22), pad(text, 16, 'right'))
        if note:
            line += '  %s' % note
        lines.append(line)

    # -- 结果 -------------------------------------------------------------
    lines.append(DOUBLE_LINE * SHEET_WIDTH)
    lines.append('【结算结果】')
    lines.append('%s%s' % (pad('医保报销合计：', 22),
                           pad('￥%s' % _money(summary.get('total_reimburse', 0)), 16, 'right')))
    lines.append('%s%s' % (pad('个人自付：', 22),
                           pad('￥%s' % _money(summary.get('personal_pay', 0)), 16, 'right')))
    lines.append('%s%s' % (pad('报销比例：', 22),
                           pad('%s%%' % summary.get('reimburse_ratio', 0), 16, 'right')))
    lines.append(DOUBLE_LINE * SHEET_WIDTH)

    # -- 计算过程 ---------------------------------------------------------
    if include_formula:
        formula = result.get('formula', {}) or {}
        steps = formula.get('steps') or []
        if steps:
            lines.append('【计算过程】')
            for index, step in enumerate(steps, start=1):
                lines.append('%d. %s' % (index, step.get('title', '')))
                lines.append('   %s' % step.get('expression', ''))
                if step.get('note'):
                    lines.append('   ⚠ %s' % step['note'])
            lines.append(LINE * SHEET_WIDTH)

    warnings = result.get('warnings') or []
    if warnings:
        lines.append('【温馨提示】')
        lines.extend('• %s' % w for w in warnings)
        lines.append(LINE * SHEET_WIDTH)

    lines.append('本单据由「医算清 MediClear-AI」自动生成，结果仅供参考。')
    lines.append('实际报销金额以医保部门核定为准。')
    lines.append(DOUBLE_LINE * SHEET_WIDTH)

    return '\n'.join(lines)


# --------------------------------------------------------------------------
# 对比表格
# --------------------------------------------------------------------------
#: 表格列定义：``(数据键, 表头, 宽度, 对齐)``
DEFAULT_COMPARISON_COLUMNS = [
    ('label', '方案', 16, 'left'),
    ('total_reimburse', '报销金额', 12, 'right'),
    ('personal_pay', '个人自付', 12, 'right'),
    ('reimburse_ratio', '报销比例', 10, 'right'),
]

#: 方案名称的候选字段（按顺序取第一个存在的）
_LABEL_KEYS = ('label', 'city_name', 'insurance_type', 'hospital_level', 'city_code')


def _normalize_columns(columns: Sequence[Any]) -> List[Tuple[str, str, int, str]]:
    """把列定义统一成 ``(键, 表头, 宽度, 对齐)`` 四元组。

    既接受完整的四元组，也接受只写列名的简写（表头即列名，宽 16、左对齐），
    这样 ``format_comparison(result, columns=['city_name', 'total_reimburse'])``
    也能直接跑。
    """
    normalized: List[Tuple[str, str, int, str]] = []
    for column in columns:
        if isinstance(column, str):
            normalized.append((column, column, 16, 'left'))
            continue
        parts = list(column)
        key = parts[0]
        header = parts[1] if len(parts) > 1 else key
        width = int(parts[2]) if len(parts) > 2 else 16
        align = parts[3] if len(parts) > 3 else 'left'
        normalized.append((key, header, width, align))
    return normalized


def _row_label(row: Mapping[str, Any]) -> str:
    """取一行方案的名字：优先 ``label``，其次城市名，最后退回代码。"""
    for key in _LABEL_KEYS:
        value = row.get(key)
        if value:
            return str(value)
    return '-'


def format_comparison(comparison: Mapping[str, Any], *,
                      title: Optional[str] = None,
                      columns: Optional[Sequence[Any]] = None) -> str:
    """把对比分析结果排成等宽表格文本。

    兼容两类来源：:class:`~mediclear.advanced.comparison.ComparisonAnalyzer`
    的返回值，以及 :func:`~mediclear.engine.compare_cities` 的返回值
    （后者没有 ``label`` 字段，方案名会退回城市名）。

    Args:
        comparison: 对比结果，需要含 ``rows``
        title: 表格标题，缺省按 ``dimension`` 自动生成
        columns: 自定义列，``(键, 表头, 宽度, 对齐)`` 四元组或直接用列名的简写

    Returns:
        str: 可直接打印的等宽表格（中文按两个字符宽对齐）
    """
    dimension = comparison.get('dimension', '')
    default_titles = {
        'city': '各城市报销对比',
        'hospital_level': '不同医院等级报销对比',
        'insurance_type': '不同医保类型报销对比',
        'chronic': '慢特病认定前后对比',
        'relocation': '异地就医备案类型对比',
    }
    title = title or default_titles.get(dimension, '方案对比')

    columns = _normalize_columns(columns or DEFAULT_COMPARISON_COLUMNS)

    inner = sum(col[2] for col in columns) + 2 * len(columns)
    width = max(SHEET_WIDTH, inner + 2)

    lines = [DOUBLE_LINE * width, pad(title, width, 'center'), DOUBLE_LINE * width]
    header = ''.join(pad(col[1], col[2] + 2, 'center') for col in columns)
    lines.append(header)
    lines.append(LINE * width)

    for row in comparison.get('rows', []):
        if 'error' in row:
            lines.append(pad('%s：%s' % (_row_label(row), row['error']), width))
            continue
        cells = []
        for key, _header, col_width, align in columns:
            value = row.get(key)
            if value is None and key == 'label':
                value = _row_label(row)
            if value is None:
                value = '-'
            if isinstance(value, float):
                value = _money(value)
            cells.append(pad(_truncate(value, col_width), col_width + 2, align))
        lines.append(''.join(cells))

    lines.append(LINE * width)
    summary = comparison.get('summary')
    if summary:
        lines.append(_wrap(summary, width))
    lines.append(DOUBLE_LINE * width)
    return '\n'.join(lines)


def _wrap(text: str, width: int) -> str:
    """按显示宽度做简单折行。"""
    words = list(str(text))
    lines: List[str] = []
    current = ''
    used = 0
    for char in words:
        char_width = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if used + char_width > width:
            lines.append(current)
            current, used = '', 0
        current += char
        used += char_width
    if current:
        lines.append(current)
    return '\n'.join(lines)
