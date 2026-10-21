# -*- coding: utf-8 -*-
"""政策数据仓库：JSON 加载 + 版本管理 + 校验 + 缓存。

目录约定::

    data/policies/
    ├── index.json            # 版本清单（哪个年份是当前版本）
    ├── 2026/
    │   ├── base.json         # 险种、就医类型、费用分类、大病、异地就医
    │   ├── cities.json       # 各城市基本医保政策
    │   └── extended.json     # 慢特病、个人账户、生育、缴费、家庭共济
    └── 2025/
        └── ...

用法::

    repo = PolicyRepository()            # 默认使用 index.json 中的 default_year
    bundle = repo.load(2025)             # 读取 2025 年历史政策
    policy = bundle.get_visit_policy('beijing', 'employee', 'inpatient')
"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, List, Mapping, Optional

from ..constants import CHRONIC_LEVEL_COMMON, INSURANCE_TYPE_CODES
from ..exceptions import (
    PolicyDataError,
    PolicyNotFoundError,
    PolicyValidationError,
    VersionNotFoundError,
)
from .schema import assert_valid, validate_policy_bundle

__all__ = ['PolicyBundle', 'PolicyRepository', 'get_repository', 'set_default_year', 'reload']

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POLICY_ROOT = os.path.join(_HERE, 'policies')

_LOCK = threading.RLock()


# --------------------------------------------------------------------------
# 一个年份的完整政策包
# --------------------------------------------------------------------------
class PolicyBundle:
    """某个年份的完整政策数据（只读视图）。"""

    def __init__(self, year: int, version: str, base: Mapping[str, Any],
                 cities: Mapping[str, Any], extended: Mapping[str, Any],
                 source_dir: str, meta: Optional[Mapping[str, Any]] = None):
        self.year = year
        self.version = version
        self.source_dir = source_dir
        self.meta = dict(meta or {})
        self._base = base or {}
        self._cities = cities or {}
        self._extended = extended or {}

    # -- 基础数据 ---------------------------------------------------------
    @property
    def insurance_types(self) -> Dict[str, Any]:
        """险种定义 ``{'employee': {...}, 'resident': {...}}``。"""
        return self._base.get('insurance_types', {})

    @property
    def visit_types(self) -> Dict[str, Any]:
        """就医类型定义 ``{'outpatient': '门诊', ...}``。"""
        return self._base.get('visit_types', {})

    @property
    def fee_categories(self) -> Dict[str, Any]:
        """费用报销类别（甲/乙/自费）及其纳入比例。"""
        return self._base.get('fee_categories', {})

    @property
    def hospital_levels(self) -> List[str]:
        """本版本支持的医院等级列表。"""
        return list(self._base.get('hospital_levels', []))

    @property
    def serious_illness(self) -> Dict[str, Any]:
        """大病保险政策。"""
        return self._base.get('serious_illness', {})

    @property
    def relocation(self) -> Dict[str, Any]:
        """异地就医政策（备案类型、渠道、材料）。"""
        return self._base.get('relocation', {})

    @property
    def chronic_diseases(self) -> Dict[str, Any]:
        """门诊慢特病配置。"""
        return self._extended.get('chronic_diseases', {})

    @property
    def personal_account(self) -> Dict[str, Any]:
        """个人账户计入规则。"""
        return self._extended.get('personal_account', {})

    @property
    def contribution(self) -> Dict[str, Any]:
        """医保缴费规则。"""
        return self._extended.get('contribution', {})

    @property
    def maternity(self) -> Dict[str, Any]:
        """生育保险与生育津贴规则。"""
        return self._extended.get('maternity', {})

    @property
    def household(self) -> Dict[str, Any]:
        """家庭账户共济规则。"""
        return self._extended.get('household', {})

    @property
    def multi_admission(self) -> Dict[str, Any]:
        """多次住院起付线递减规则。"""
        return self._extended.get('multi_admission', {})

    # -- 城市政策 ---------------------------------------------------------
    @property
    def cities(self) -> Dict[str, Any]:
        """全部城市政策原始字典（键为城市代码）。"""
        return self._cities

    def has_city(self, city_code: str) -> bool:
        """城市是否在本版本政策库中。"""
        return city_code in self._cities

    def city(self, city_code: str) -> Dict[str, Any]:
        """获取城市政策原始字典。

        Raises:
            PolicyNotFoundError: 城市不存在。
        """
        if city_code not in self._cities:
            raise PolicyNotFoundError('未收录城市 %r' % city_code, city=city_code)
        return self._cities[city_code]

    def supported_insurance_types(self, city_code: str) -> List[str]:
        """该城市支持的险种列表。"""
        city = self.city(city_code)
        return [code for code in INSURANCE_TYPE_CODES if code in city]

    def supported_visit_types(self, city_code: str, insurance_type: str) -> List[str]:
        """该城市 + 险种支持的就医类型列表。"""
        city = self.city(city_code)
        return sorted((city.get(insurance_type) or {}).keys())

    def get_city_policy(self, city_code: str, insurance_type: str) -> Optional[Dict[str, Any]]:
        """获取城市 + 险种政策，不存在返回 ``None``（不抛异常）。"""
        return (self._cities.get(city_code) or {}).get(insurance_type)

    def get_visit_policy(self, city_code: str, insurance_type: str,
                         visit_type: str) -> Optional[Dict[str, Any]]:
        """获取城市 + 险种 + 就医类型的政策，不存在返回 ``None``。"""
        return (self.get_city_policy(city_code, insurance_type) or {}).get(visit_type)

    def require_visit_policy(self, city_code: str, insurance_type: str,
                             visit_type: str) -> Dict[str, Any]:
        """同 :meth:`get_visit_policy`，但不存在时抛出异常。"""
        policy = self.get_visit_policy(city_code, insurance_type, visit_type)
        if not policy:
            city_name = (self._cities.get(city_code) or {}).get('name', city_code)
            raise PolicyNotFoundError(
                '未找到 %s / %s / %s 的医保政策' % (city_name, insurance_type, visit_type),
                city=city_code, insurance_type=insurance_type, visit_type=visit_type,
            )
        return policy

    def list_cities(self) -> List[Dict[str, Any]]:
        """列出全部城市（含代码、名称、省份、级别）。"""
        return [
            {
                'code': code,
                'name': policy.get('name', code),
                'province': policy.get('province', ''),
                'tier': policy.get('tier', ''),
            }
            for code, policy in self._cities.items()
        ]

    def cities_by_tier(self, tier: str) -> List[Dict[str, Any]]:
        """按城市级别（一线 / 新一线 / 二线 ...）筛选城市。"""
        return [c for c in self.list_cities() if c['tier'] == tier]

    def tiers(self) -> List[str]:
        """全部出现过的城市级别。"""
        seen: List[str] = []
        for city in self.list_cities():
            if city['tier'] and city['tier'] not in seen:
                seen.append(city['tier'])
        return seen

    # -- 慢特病 -----------------------------------------------------------
    def list_chronic_diseases(self) -> List[Dict[str, Any]]:
        """列出全部门诊慢特病病种（不含城市差异）。"""
        diseases = self.chronic_diseases.get('diseases', {})
        return [
            {
                'code': code,
                'name': info.get('name', code),
                'level': info.get('level', CHRONIC_LEVEL_COMMON),
                'default': dict(info.get('default') or {}),
            }
            for code, info in diseases.items()
        ]

    def resolve_chronic_disease(self, disease_code: str, city_code: str) -> Dict[str, Any]:
        """合并"病种默认值"与"城市覆盖值"，得到该城市下的实际待遇。

        Raises:
            PolicyNotFoundError: 病种不存在。
        """
        diseases = self.chronic_diseases.get('diseases', {})
        if disease_code not in diseases:
            raise PolicyNotFoundError('未知的门诊慢特病病种 %r' % disease_code)

        info = diseases[disease_code]
        merged = {
            'code': disease_code,
            'name': info.get('name', disease_code),
            'level': info.get('level', CHRONIC_LEVEL_COMMON),
        }
        merged.update(info.get('default') or {})
        override = (info.get('cities') or {}).get(city_code) or {}
        merged.update(override)
        merged['city_code'] = city_code
        merged['is_city_override'] = bool(override)
        return merged

    # -- 缴费基数 ---------------------------------------------------------
    def contribution_base_limits(self, city_code: str) -> Dict[str, float]:
        """获取该城市的社保缴费基数上下限（元/月）。

        未单独配置的城市回退到全国默认区间。
        """
        limits = (self.contribution.get('base_limits') or {}).get(city_code)
        if not limits:
            return {'min': 3000.0, 'max': 20000.0, 'is_default': True}
        return {
            'min': float(limits['min']),
            'max': float(limits['max']),
            'is_default': False,
        }

    # -- 描述 -------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """版本摘要，用于前端展示"当前使用哪一版政策"。"""
        return {
            'year': self.year,
            'version': self.version,
            'source_dir': self.source_dir,
            'city_count': len(self._cities),
            'meta': dict(self.meta),
        }

    def __repr__(self):  # pragma: no cover - 调试友好
        return '<PolicyBundle %s cities=%d>' % (self.version, len(self._cities))


# --------------------------------------------------------------------------
# 仓库
# --------------------------------------------------------------------------
class PolicyRepository:
    """政策数据仓库。

    Args:
        root: 政策目录，默认 ``mediclear/data/policies``
        year: 默认年份，``None`` 表示读 ``index.json`` 的 ``default_year``
        auto_validate: 加载时是否自动校验（校验不通过抛
            :class:`PolicyValidationError`）
    """

    def __init__(self, root: Optional[str] = None, year: Optional[int] = None,
                 auto_validate: bool = True):
        self.root = os.path.abspath(root or DEFAULT_POLICY_ROOT)
        self.auto_validate = auto_validate
        self._cache: Dict[int, PolicyBundle] = {}
        self._index: Optional[Dict[str, Any]] = None
        self._default_year = year

    # -- 版本清单 ---------------------------------------------------------
    @property
    def index(self) -> Dict[str, Any]:
        """读取 ``index.json``（带缓存）。"""
        if self._index is None:
            path = os.path.join(self.root, 'index.json')
            self._index = self._read_json(path)
        return self._index

    def list_versions(self) -> List[Dict[str, Any]]:
        """列出全部政策版本。"""
        return [dict(v) for v in self.index.get('versions', [])]

    def available_years(self) -> List[int]:
        """全部可用年份（升序）。"""
        return sorted(int(v['year']) for v in self.index.get('versions', []))

    @property
    def default_year(self) -> int:
        """当前默认年份。"""
        if self._default_year is not None:
            return self._default_year
        return int(self.index.get('default_year') or max(self.available_years()))

    def use_version(self, year: int) -> PolicyBundle:
        """切换默认政策版本并返回该版本数据。"""
        bundle = self.load(year)
        self._default_year = bundle.year
        return bundle

    @property
    def current(self) -> PolicyBundle:
        """当前默认版本的政策包（首次访问时加载）。"""
        return self.load(self.default_year)

    # -- 加载 -------------------------------------------------------------
    def load(self, year: Optional[int] = None) -> PolicyBundle:
        """加载指定年份政策（带缓存）。

        Args:
            year: ``None`` 表示使用默认年份

        Raises:
            VersionNotFoundError: 该年份不存在
            PolicyValidationError: ``auto_validate=True`` 且校验失败
        """
        year = int(year if year is not None else self.default_year)
        with _LOCK:
            if year in self._cache:
                return self._cache[year]

            directory = os.path.join(self.root, str(year))
            if not os.path.isdir(directory):
                raise VersionNotFoundError(
                    '政策版本 %d 不存在（目录：%s），可用版本：%s'
                    % (year, directory, self.available_years())
                )

            base = self._read_json(os.path.join(directory, 'base.json'))
            cities = self._read_json(os.path.join(directory, 'cities.json'))
            extended = self._read_json(os.path.join(directory, 'extended.json'))

            if self.auto_validate:
                assert_valid(validate_policy_bundle(
                    base, cities, extended, source='policy-%d' % year))

            version = ((cities.get('meta') or {}).get('version')
                       or (base.get('meta') or {}).get('version')
                       or '%d.0' % year)

            bundle = PolicyBundle(
                year=year,
                version=version,
                base=base,
                cities=cities.get('cities') or {},
                extended=extended,
                source_dir=directory,
                meta=cities.get('meta') or base.get('meta') or {},
            )
            self._cache[year] = bundle
            return bundle

    def reload(self, year: Optional[int] = None) -> PolicyBundle:
        """清除缓存后重新加载（改完 JSON 不必重启进程）。"""
        with _LOCK:
            if year is None:
                self._cache.clear()
                self._index = None
                return self.load()
            self._cache.pop(int(year), None)
            return self.load(year)

    def validate(self, year: Optional[int] = None) -> Dict[str, Any]:
        """对指定版本做一次完整校验，返回报告而不抛异常。

        与 ``auto_validate`` 的区别：这里即使有 error 也会返回报告，
        适合放在 CI 或管理后台里做"体检"。
        """
        year = int(year if year is not None else self.default_year)
        directory = os.path.join(self.root, str(year))
        if not os.path.isdir(directory):
            raise VersionNotFoundError('政策版本 %d 不存在' % year)
        report = validate_policy_bundle(
            self._read_json(os.path.join(directory, 'base.json')),
            self._read_json(os.path.join(directory, 'cities.json')),
            self._read_json(os.path.join(directory, 'extended.json')),
            source='policy-%d' % year,
        )
        report['year'] = year
        report['ok'] = not report['errors']
        return report

    # -- 内部 -------------------------------------------------------------
    @staticmethod
    def _read_json(path: str) -> Dict[str, Any]:
        if not os.path.isfile(path):
            raise PolicyDataError('政策数据文件不存在：%s' % path)
        try:
            with open(path, 'r', encoding='utf-8') as fp:
                return json.load(fp)
        except json.JSONDecodeError as exc:
            raise PolicyDataError('政策数据文件解析失败：%s（%s）' % (path, exc))
        except OSError as exc:  # pragma: no cover - 磁盘异常
            raise PolicyDataError('政策数据文件读取失败：%s（%s）' % (path, exc))


# --------------------------------------------------------------------------
# 模块级默认仓库
# --------------------------------------------------------------------------
_repository: Optional[PolicyRepository] = None


def get_repository(root: Optional[str] = None, year: Optional[int] = None) -> PolicyRepository:
    """获取全局默认仓库（单例）。

    Args:
        root: 首次调用时指定政策目录；后续调用忽略
        year: 首次调用时指定默认年份；后续调用忽略
    """
    global _repository
    with _LOCK:
        if _repository is None or (root and _repository.root != os.path.abspath(root)):
            _repository = PolicyRepository(root=root, year=year)
        return _repository


def set_default_year(year: int) -> PolicyBundle:
    """切换全局默认政策版本，返回该版本政策包。

    典型用法：新年度政策下发后，把系统切到新版本而无需改代码。
    """
    return get_repository().use_version(year)


def reload(year: Optional[int] = None) -> PolicyBundle:
    """清空全局缓存并重新加载。"""
    return get_repository().reload(year)


def _reset_for_tests() -> None:  # pragma: no cover - 测试辅助
    """重置全局单例（仅测试使用）。"""
    global _repository
    with _LOCK:
        _repository = None


# 便于外部构造"改一份数据看看会怎样"的实验
def clone_bundle(bundle: PolicyBundle) -> PolicyBundle:
    """深拷贝一份政策包，用于沙箱式试算（不污染全局缓存）。"""
    return PolicyBundle(
        year=bundle.year,
        version=bundle.version + '-clone',
        base=copy.deepcopy(bundle._base),
        cities=copy.deepcopy(bundle._cities),
        extended=copy.deepcopy(bundle._extended),
        source_dir=bundle.source_dir,
        meta=copy.deepcopy(bundle.meta),
    )
