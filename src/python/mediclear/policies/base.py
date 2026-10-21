# -*- coding: utf-8 -*-
"""险种计算模块的公共基类与工具。

新增一个险种 / 城市的正确姿势：

1. 在政策 JSON 里加数据（不用改代码）；
2. 如果计算规则本身是新的，就在本包下新建一个模块，继承
   :class:`PolicyModule`，实现 ``calculate()``；
3. 用 :func:`register_module` 注册，便于统一发现与展示。

本模块提供三样东西：

* :class:`TierTable` —— 分段报销计算（起付线之上的费用按区间比例累加）
* :class:`PolicyModule` —— 险种计算模块基类
* 一组金额/比例取整与公式描述辅助函数
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..constants import MONEY_PRECISION, RATIO_PRECISION
from ..data.repository import PolicyBundle
from ..exceptions import PolicyNotFoundError

__all__ = [
    'TierTable',
    'PolicyModule',
    'register_module',
    'get_module',
    'list_modules',
    'round_money',
    'round_ratio',
    'resolve_tiers',
    'format_formula',
]


# --------------------------------------------------------------------------
# 数值辅助
# --------------------------------------------------------------------------
def round_money(value: float) -> float:
    """金额取整到分。"""
    return round(float(value) + 0.0, MONEY_PRECISION)


def round_ratio(value: float) -> float:
    """比例取整到 0.1 个百分点（输入为 0~1 的小数，输出仍是小数）。"""
    return round(float(value), RATIO_PRECISION + 2)


def format_formula(text: str, **kwargs: Any) -> str:
    """安全的公式模板渲染（缺失变量时保留占位符而不是抛异常）。"""
    class _Safe(dict):
        def __missing__(self, key):  # pragma: no cover - 缺变量兜底
            return '{%s}' % key

    return text.format_map(_Safe(**kwargs))


# --------------------------------------------------------------------------
# 分段报销表
# --------------------------------------------------------------------------
class TierTable:
    """分段报销比例表。

    医疗保险的"分段报销"是**超额累进**：费用落在哪一段，那一段就按该段比例报销。
    例如北京职工住院三级医院：0~3 万报 85%，3~4 万报 90%，4 万以上报 95%。

        >>> table = TierTable([
        ...     {'min': 0, 'max': 30000, 'rate': 0.85},
        ...     {'min': 30000, 'max': 40000, 'rate': 0.90},
        ...     {'min': 40000, 'max': 300000, 'rate': 0.95},
        ... ])
        >>> round(table.reimburse(50000), 2)
        44000.0

    Args:
        tiers: 分段列表，每项含 ``min`` / ``max`` / ``rate``（可选 ``location``）
        location: 只保留该医院等级的分段；``None`` 表示不过滤
        name: 名称，用于生成公式说明
    """

    def __init__(self, tiers: Sequence[Mapping[str, Any]], location: Optional[str] = None,
                 name: str = '报销比例'):
        selected = [t for t in (tiers or []) if location is None or t.get('location') == location]
        self.name = name
        self.location = location
        self.tiers: List[Dict[str, float]] = sorted(
            (
                {
                    'min': float(t.get('min', 0)),
                    'max': float(t.get('max', 0)),
                    'rate': float(t.get('rate', 0)),
                }
                for t in selected
            ),
            key=lambda t: t['min'],
        )

    def __bool__(self) -> bool:
        return bool(self.tiers)

    def __len__(self) -> int:
        return len(self.tiers)

    @property
    def min_rate(self) -> float:
        """最低档比例（费用越高越划算时，这是"起步"比例）。"""
        return min((t['rate'] for t in self.tiers), default=0.0)

    @property
    def max_rate(self) -> float:
        """最高档比例。"""
        return max((t['rate'] for t in self.tiers), default=0.0)

    def rate_at(self, amount: float) -> float:
        """费用达到 ``amount`` 时，**最后一档**适用的比例。"""
        rate = 0.0
        for tier in self.tiers:
            if amount > tier['min']:
                rate = tier['rate']
            else:
                break
        return rate

    def marginal_rate(self, amount: float) -> float:
        """再增加 1 元费用时适用的比例（用于"再花多少能到下一档"的建议）。

        区间按 ``[min, max)`` 取，因此费用恰好落在档位边界上时，
        返回的是**下一档**的比例（多花的这 1 元进的是下一档）。
        """
        for tier in self.tiers:
            if tier['min'] <= amount < tier['max']:
                return tier['rate']
        return self.max_rate

    def reimburse(self, amount: float) -> float:
        """按超额累进计算报销金额。"""
        return self.breakdown(amount)['reimburse']

    def breakdown(self, amount: float) -> Dict[str, Any]:
        """返回报销金额 + 每段明细，便于向用户解释"钱是怎么算出来的"。

        Returns:
            ``{'reimburse', 'segments': [{'min','max','amount','rate','reimburse'}],
            'effective_rate', 'top_rate'}``
        """
        amount = max(0.0, float(amount))
        segments: List[Dict[str, Any]] = []
        total = 0.0

        for tier in self.tiers:
            low, high, rate = tier['min'], tier['max'], tier['rate']
            if amount <= low:
                break
            chunk = min(amount, high) - low
            if chunk <= 0:
                continue
            part = chunk * rate
            total += part
            segments.append({
                'min': low,
                'max': high,
                'amount': round_money(chunk),
                'rate': rate,
                'reimburse': round_money(part),
                'formula': '%s × %s%% = %s' % (
                    round_money(chunk), round(rate * 100, 2), round_money(part)),
            })

        return {
            'name': self.name,
            'location': self.location,
            'base': round_money(amount),
            'reimburse': round_money(total),
            'effective_rate': round(total / amount, 4) if amount > 0 else 0.0,
            'top_rate': self.rate_at(amount),
            'segments': segments,
        }

    def describe(self) -> List[Dict[str, Any]]:
        """返回分段表本身（用于前端画"报销比例阶梯"）。"""
        return [
            {
                'min': t['min'],
                'max': t['max'],
                'rate': t['rate'],
                'rate_percent': round(t['rate'] * 100, RATIO_PRECISION),
                'label': '%s ~ %s 元报 %s%%' % (
                    round_money(t['min']), round_money(t['max']),
                    round(t['rate'] * 100, RATIO_PRECISION)),
            }
            for t in self.tiers
        ]

    def __repr__(self):  # pragma: no cover - 调试友好
        return '<TierTable %s %d tiers>' % (self.location or 'all', len(self.tiers))


def resolve_tiers(policy: Mapping[str, Any], hospital_level: Optional[str]) -> Tuple[str, List[Dict[str, Any]]]:
    """从政策里取出某个医院等级的分段表。

    医院等级缺省时使用政策自带的 ``default_hospital_level``；
    若该等级没有配置分段，则退化为政策中的全部分段。

    Returns:
        ``(实际使用的医院等级, 分段列表)``
    """
    all_tiers = list(policy.get('reimburse_rates') or [])
    level = hospital_level or policy.get('default_hospital_level')

    if hospital_level:
        matched = [t for t in all_tiers if t.get('location') == hospital_level]
        if matched:
            return hospital_level, matched
        # 传了不存在的等级：退化为默认等级
    default_level = policy.get('default_hospital_level')
    matched = [t for t in all_tiers if t.get('location') == default_level]
    if matched:
        return default_level, matched

    level = all_tiers[0].get('location') if all_tiers else (level or '')
    return level, all_tiers


# --------------------------------------------------------------------------
# 险种模块基类
# --------------------------------------------------------------------------
class PolicyModule(ABC):
    """险种计算模块基类。

    子类需要定义 :attr:`code` / :attr:`name` 并实现 :meth:`calculate`。

    Attributes:
        code: 模块代码，如 ``'basic'`` / ``'maternity'``
        name: 中文名，如 ``'基本医疗保险'``
    """

    code: str = ''
    name: str = ''

    def __init__(self, bundle: PolicyBundle):
        """
        Args:
            bundle: 政策数据包（由 :class:`~mediclear.data.repository.PolicyRepository` 提供）
        """
        if not isinstance(bundle, PolicyBundle):
            raise TypeError('PolicyModule 需要 PolicyBundle，收到 %r' % type(bundle).__name__)
        self.bundle = bundle

    # -- 子类实现 ---------------------------------------------------------
    @abstractmethod
    def calculate(self, **kwargs: Any) -> Dict[str, Any]:
        """执行计算，返回可直接序列化的结果字典。"""

    # -- 通用能力 ---------------------------------------------------------
    def enabled(self) -> bool:
        """本险种在当前政策版本下是否启用。"""
        return True

    def describe(self) -> Dict[str, Any]:
        """模块描述，用于前端展示"这个险种是怎么算的"。"""
        return {
            'code': self.code,
            'name': self.name,
            'enabled': self.enabled(),
            'policy_version': self.bundle.version,
            'policy_year': self.bundle.year,
        }

    def require_city(self, city_code: str) -> Dict[str, Any]:
        """取城市政策，不存在时抛 :class:`PolicyNotFoundError`。"""
        return self.bundle.city(city_code)

    def __repr__(self):  # pragma: no cover - 调试友好
        return '<%s %s>' % (type(self).__name__, self.bundle.version)


# --------------------------------------------------------------------------
# 模块注册表（便于"新增险种"被自动发现）
# --------------------------------------------------------------------------
_REGISTRY: Dict[str, type] = {}


def register_module(cls: type) -> type:
    """把险种模块登记到注册表（可作类装饰器使用）。"""
    if not issubclass(cls, PolicyModule):
        raise TypeError('只能注册 PolicyModule 子类，收到 %r' % cls)
    if not cls.code:
        raise ValueError('注册的模块必须定义 code')
    _REGISTRY[cls.code] = cls
    return cls


def get_module(code: str) -> type:
    """按代码取模块类。"""
    if code not in _REGISTRY:
        raise PolicyNotFoundError('未注册的险种模块 %r，可用：%s'
                                  % (code, sorted(_REGISTRY)))
    return _REGISTRY[code]


def list_modules() -> List[Dict[str, str]]:
    """列出全部已注册模块。"""
    return [{'code': code, 'name': cls.name} for code, cls in sorted(_REGISTRY.items())]


def registered_codes() -> Iterable[str]:  # pragma: no cover - 调试辅助
    return tuple(sorted(_REGISTRY))
