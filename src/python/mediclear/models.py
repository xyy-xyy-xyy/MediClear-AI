# -*- coding: utf-8 -*-
"""输入数据模型。

对外接口（``calculator.quick_calculate`` 等）历史上接收的是 ``dict`` 列表，
为了向后兼容，本模块的 :class:`FeeItem` 同时支持 dataclass 与 dict 两种输入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .constants import (
    CLASS_A,
    DEFAULT_ITEM_TYPE,
    FEE_CLASS_CODES,
    ITEM_TYPE_ALIASES,
    ITEM_TYPES,
    MONEY_PRECISION,
)
from .exceptions import InvalidInputError

__all__ = ['FeeItem', 'normalize_fee_items', 'resolve_item_type', 'resolve_category']


def resolve_item_type(raw: Any) -> str:
    """把用户写的费用类型（代码或中文）归一化为项目类型代码。

    >>> resolve_item_type('药品')
    'drug'
    >>> resolve_item_type('consumable')
    'consumable'
    >>> resolve_item_type(None)
    'other'
    """
    if not raw:
        return DEFAULT_ITEM_TYPE
    text = str(raw).strip()
    if text in ITEM_TYPES:
        return text
    if text in ITEM_TYPE_ALIASES:
        return ITEM_TYPE_ALIASES[text]
    lowered = text.lower()
    if lowered in ITEM_TYPES:
        return lowered
    return DEFAULT_ITEM_TYPE


def resolve_category(category: Any, item_type: str) -> str:
    """推断费用所属的报销类别（甲类 / 乙类 / 自费）。

    优先使用显式给出的 ``category``；否则按项目类型的默认类别推断。
    """
    if category:
        text = str(category).strip()
        aliases = {
            '甲类': 'class_a', '甲': 'class_a', 'a': 'class_a', 'A': 'class_a',
            '乙类': 'class_b', '乙': 'class_b', 'b': 'class_b', 'B': 'class_b',
            '自费': 'self_pay', '丙类': 'self_pay', 'c': 'self_pay', 'C': 'self_pay',
        }
        text = aliases.get(text, text)
        if text in FEE_CLASS_CODES:
            return text
    return ITEM_TYPES.get(item_type, ITEM_TYPES[DEFAULT_ITEM_TYPE])['default_class']


@dataclass
class FeeItem:
    """一条医疗费用明细。

    Attributes:
        name: 项目名称，例如 ``'阿莫西林胶囊'``
        amount: 该项目的总金额（元），必须 >= 0
        category: 报销类别，``class_a`` / ``class_b`` / ``self_pay``；
            留空则按 ``item_type`` 推断
        item_type: 项目类型，见 :data:`mediclear.constants.ITEM_TYPES`；
            支持中文别名（``'药品'`` / ``'检查'`` ...）
        quantity: 数量，仅用于明细单展示
        unit_price: 单价，留空则由 ``amount / quantity`` 推算
        note: 备注
    """

    name: str
    amount: float
    category: str = ''
    item_type: str = DEFAULT_ITEM_TYPE
    quantity: float = 1.0
    unit_price: Optional[float] = None
    note: str = ''

    def __post_init__(self):
        self.item_type = resolve_item_type(self.item_type)
        self.category = resolve_category(self.category, self.item_type)
        self.amount = _as_money(self.amount, field_name='amount')
        if self.quantity in (None, 0):
            self.quantity = 1.0
        if self.unit_price is None and self.quantity:
            self.unit_price = round(self.amount / self.quantity, 6)

    # -- 展示辅助 ---------------------------------------------------------
    @property
    def item_type_name(self) -> str:
        """项目类型中文名。"""
        return ITEM_TYPES.get(self.item_type, ITEM_TYPES[DEFAULT_ITEM_TYPE])['name']

    def to_dict(self) -> Dict[str, Any]:
        """转回普通 dict（便于序列化与向后兼容调用方）。"""
        return {
            'name': self.name,
            'amount': self.amount,
            'category': self.category,
            'item_type': self.item_type,
            'item_type_name': self.item_type_name,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'note': self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> 'FeeItem':
        """由 dict 构造。

        兼容 ``type`` / ``item_type`` / ``type_name`` 三种类型字段写法，
        并容忍缺失 ``category``（按类型推断）。
        ``amount`` 可以省略——只要同时给出 ``quantity`` 与 ``unit_price``，
        金额会按 ``数量 × 单价`` 推算。

        Raises:
            InvalidInputError: 缺少 ``name`` / ``amount`` 或数值非法。
        """
        if not isinstance(data, Mapping):
            raise InvalidInputError('费用明细必须是 dict 或 FeeItem，收到 %r' % (type(data).__name__,))

        quantity = data.get('quantity', 1) or 1
        unit_price = data.get('unit_price')
        if 'amount' not in data:
            if unit_price is None:
                raise InvalidInputError('费用明细缺少 amount 字段：%r'
                                        % (data.get('name', data),))
            amount = _as_money(quantity, field_name='quantity') * _as_money(
                unit_price, field_name='unit_price')
        else:
            amount = data['amount']

        raw_type = (
            data.get('item_type')
            or data.get('type')
            or data.get('type_name')
            or DEFAULT_ITEM_TYPE
        )
        return cls(
            name=str(data.get('name') or '未命名项目'),
            amount=amount,
            category=data.get('category', ''),
            item_type=raw_type,
            quantity=quantity,
            unit_price=unit_price,
            note=str(data.get('note', '') or ''),
        )


def _as_money(value: Any, *, field_name: str) -> float:
    """把入参转成非负金额，非法则抛 :class:`InvalidInputError`。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise InvalidInputError('%s 必须是数字，收到 %r' % (field_name, value))
    value = float(value)
    if value != value or value in (float('inf'), float('-inf')):
        raise InvalidInputError('%s 不能是 NaN / Infinity' % field_name)
    if value < 0:
        raise InvalidInputError('%s 不能为负数：%s' % (field_name, value))
    return round(value, MONEY_PRECISION)


def normalize_fee_items(items: Iterable[Any]) -> List[FeeItem]:
    """把任意形式的费用清单统一成 :class:`FeeItem` 列表。

    Args:
        items: ``FeeItem``、``dict`` 或二者的混合序列

    Returns:
        list[FeeItem]

    Raises:
        InvalidInputError: 任一条目格式非法。
    """
    if items is None:
        return []
    if isinstance(items, (FeeItem, Mapping)):
        items = [items]

    result: List[FeeItem] = []
    for index, raw in enumerate(items):
        if isinstance(raw, FeeItem):
            result.append(raw)
            continue
        try:
            result.append(FeeItem.from_dict(raw))
        except InvalidInputError as exc:
            raise InvalidInputError('第 %d 条费用明细有误：%s' % (index + 1, exc))
    return result


#: 供外部构造示例数据使用的默认类别
DEFAULT_CATEGORY = CLASS_A
