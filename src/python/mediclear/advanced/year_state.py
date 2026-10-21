# -*- coding: utf-8 -*-
"""年度累计状态：多次就医时"今年已经用掉多少额度"的记忆。

医疗保险的很多规则都是**按年度累计**的：

* 起付线：同一年度内多次住院时按次数递减，跨年重置
* 封顶线：统筹基金年度报销总额
* 大病保险起付线：按年度累计的个人自付额计算

单次计算函数无法表达这些规则，所以这里引入 :class:`YearState`，
由调用方在一次年度模拟中反复传入、逐次更新。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..policies.base import round_money

__all__ = ['YearState', 'VisitRecord']


@dataclass
class VisitRecord:
    """一次就医的记录（住院或门诊）。"""

    index: int
    visit_type: str
    total_fee: float
    reimburse_base: float
    deductible: float
    reimburse: float
    serious_reimburse: float
    personal_pay: float
    hospital_level: str = ''
    description: str = ''
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_reimburse(self) -> float:
        """本次基本医保 + 大病保险的报销合计。"""
        return round_money(self.reimburse + self.serious_reimburse)

    def to_dict(self) -> Dict[str, Any]:
        """转为可序列化字典。"""
        return {
            'index': self.index,
            'visit_type': self.visit_type,
            'description': self.description,
            'hospital_level': self.hospital_level,
            'total_fee': self.total_fee,
            'reimburse_base': self.reimburse_base,
            'deductible': self.deductible,
            'reimburse': self.reimburse,
            'serious_reimburse': self.serious_reimburse,
            'total_reimburse': self.total_reimburse,
            'personal_pay': self.personal_pay,
            **self.extra,
        }


@dataclass
class YearState:
    """一个参保人某一医保年度内的累计状态。

    Attributes:
        year: 医保年度
        total_fee: 年度累计医疗费用
        total_reimburse: 年度累计统筹报销（含大病）
        total_personal_pay: 年度累计个人自付
        deductible_paid: 年度累计已支付的住院起付线
        admission_count: 年度住院次数（决定起付线递减档位）
        outpatient_count: 年度门诊次数
        visits: 逐次就医记录
    """

    year: int = 0
    total_fee: float = 0.0
    total_reimburse: float = 0.0
    total_personal_pay: float = 0.0
    deductible_paid: float = 0.0
    admission_count: int = 0
    outpatient_count: int = 0
    visits: List[VisitRecord] = field(default_factory=list)

    # -- 更新 ------------------------------------------------------------
    def add_visit(self, record: VisitRecord) -> None:
        """记录一次就医并更新累计值。"""
        self.visits.append(record)
        self.total_fee = round_money(self.total_fee + record.total_fee)
        self.total_reimburse = round_money(self.total_reimburse + record.total_reimburse)
        self.total_personal_pay = round_money(self.total_personal_pay + record.personal_pay)
        if record.visit_type == 'inpatient':
            self.admission_count += 1
            self.deductible_paid = round_money(self.deductible_paid + record.deductible)
        else:
            self.outpatient_count += 1

    # -- 派生指标 ---------------------------------------------------------
    @property
    def overall_ratio(self) -> float:
        """年度整体报销比例（百分数）。"""
        return round(self.total_reimburse / self.total_fee * 100, 1) if self.total_fee else 0.0

    def remaining_quota(self, max_reimburse: float) -> float:
        """统筹基金年度封顶线的剩余额度。"""
        return round_money(max(0.0, float(max_reimburse) - self.total_reimburse))

    def to_dict(self) -> Dict[str, Any]:
        """摘要（不含逐次明细）。"""
        return {
            'year': self.year,
            'total_fee': self.total_fee,
            'total_reimburse': self.total_reimburse,
            'total_personal_pay': self.total_personal_pay,
            'deductible_paid': self.deductible_paid,
            'admission_count': self.admission_count,
            'outpatient_count': self.outpatient_count,
            'visit_count': len(self.visits),
            'overall_ratio': self.overall_ratio,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'YearState':
        """由摘要字典恢复状态（用于"年中接入"的场景）。"""
        return cls(
            year=int(data.get('year', 0) or 0),
            total_fee=float(data.get('total_fee', 0) or 0),
            total_reimburse=float(data.get('total_reimburse', 0) or 0),
            total_personal_pay=float(data.get('total_personal_pay', 0) or 0),
            deductible_paid=float(data.get('deductible_paid', 0) or 0),
            admission_count=int(data.get('admission_count', 0) or 0),
            outpatient_count=int(data.get('outpatient_count', 0) or 0),
        )
