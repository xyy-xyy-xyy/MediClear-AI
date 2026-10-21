# -*- coding: utf-8 -*-
"""政策数据校验。

政策数据一旦写错（比例写成 8.5 而不是 0.85、分段区间重叠、限额为负……），
计算结果会静默出错。本模块在加载时做结构与语义两层校验：

* **结构层**：字段是否存在、类型是否正确
* **语义层**：比例是否落在 [0, 1]、分段区间是否连续且不重叠、上下限是否倒挂等

校验结果分为 ``errors``（必须修复）与 ``warnings``（可疑但可运行）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from ..constants import FEE_CLASS_CODES, INSURANCE_TYPE_CODES, VISIT_TYPE_CODES
from ..exceptions import PolicyValidationError

__all__ = ['validate_policy_bundle', 'assert_valid', 'Issue']


class Issue(dict):
    """一条校验问题：``{'level', 'path', 'message'}``。"""

    def __init__(self, level: str, path: str, message: str):
        super().__init__(level=level, path=path, message=message)

    def __repr__(self):  # pragma: no cover - 调试友好
        return '<%s %s %s>' % (self['level'], self['path'], self['message'])


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _err(issues: List[Issue], path: str, message: str) -> None:
    issues.append(Issue('error', path, message))


def _warn(issues: List[Issue], path: str, message: str) -> None:
    issues.append(Issue('warning', path, message))


# --------------------------------------------------------------------------
# 各数据段校验
# --------------------------------------------------------------------------
def _validate_base(base: Mapping[str, Any], issues: List[Issue]) -> None:
    if not isinstance(base, Mapping):
        _err(issues, 'base', '政策基础数据必须是对象')
        return

    for key in ('insurance_types', 'visit_types', 'fee_categories'):
        if not isinstance(base.get(key), Mapping) or not base.get(key):
            _err(issues, 'base.%s' % key, '必须是非空对象')

    insurance_types = base.get('insurance_types') or {}
    for code in INSURANCE_TYPE_CODES:
        if code not in insurance_types:
            _err(issues, 'base.insurance_types', '缺少医保类型 %r' % code)

    visit_types = base.get('visit_types') or {}
    for code in VISIT_TYPE_CODES:
        if code not in visit_types:
            _warn(issues, 'base.visit_types', '未定义就医类型 %r，涉及该类型的城市政策将无法查询' % code)

    fee_categories = base.get('fee_categories') or {}
    for code, info in fee_categories.items():
        if code not in FEE_CLASS_CODES:
            _warn(issues, 'base.fee_categories.%s' % code, '未知的报销类别代码')
        ratio = (info or {}).get('reimburse_ratio_base')
        if not _is_number(ratio) or not 0 <= ratio <= 1:
            _err(issues, 'base.fee_categories.%s.reimburse_ratio_base' % code,
                 '必须为 0~1 之间的数，当前为 %r' % (ratio,))

    serious = base.get('serious_illness') or {}
    if serious.get('enabled'):
        if not _is_number(serious.get('deductible')) or serious.get('deductible', -1) < 0:
            _err(issues, 'base.serious_illness.deductible', '起付线必须是非负数')
        rate = serious.get('reimburse_rate')
        if not _is_number(rate) or not 0 <= rate <= 1:
            _err(issues, 'base.serious_illness.reimburse_rate',
                 '报销比例必须为 0~1 之间的数，当前为 %r' % (rate,))
        cap = serious.get('max_reimburse')
        if not _is_number(cap) or cap <= 0:
            _err(issues, 'base.serious_illness.max_reimburse', '封顶线必须是正数')


def _validate_tiers(tiers: Sequence[Mapping[str, Any]], path: str, issues: List[Issue]) -> None:
    """校验分段报销比例：数值合法 + 同一医院等级内区间连续不重叠。"""
    by_location: Dict[str, List[Mapping[str, Any]]] = {}
    for index, tier in enumerate(tiers):
        tpath = '%s[%d]' % (path, index)
        if not isinstance(tier, Mapping):
            _err(issues, tpath, '分段必须是对象')
            continue

        low, high, rate = tier.get('min'), tier.get('max'), tier.get('rate')
        location = tier.get('location')
        if not isinstance(location, str) or not location:
            _err(issues, tpath + '.location', '缺少医院等级 location')
        if not _is_number(low) or low < 0:
            _err(issues, tpath + '.min', '区间下界必须是非负数，当前为 %r' % (low,))
        if not _is_number(high):
            _err(issues, tpath + '.max', '区间上界必须是数字，当前为 %r' % (high,))
        elif _is_number(low) and high <= low:
            _err(issues, tpath, '区间上界(%s)必须大于下界(%s)' % (high, low))
        if not _is_number(rate) or not 0 <= rate <= 1:
            _err(issues, tpath + '.rate', '报销比例必须为 0~1 之间的数，当前为 %r' % (rate,))

        if isinstance(location, str) and _is_number(low) and _is_number(high):
            by_location.setdefault(location, []).append(tier)

    for location, group in by_location.items():
        ordered = sorted(group, key=lambda t: t['min'])
        if ordered[0]['min'] != 0:
            _err(issues, '%s[%s]' % (path, location),
                 '分段区间必须从 0 开始，当前最小下界为 %s' % ordered[0]['min'])
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev['max'] != nxt['min']:
                _err(issues, '%s[%s]' % (path, location),
                     '分段区间不连续：上一段止于 %s，下一段始于 %s' % (prev['max'], nxt['min']))
        rates = [t['rate'] for t in ordered]
        if rates != sorted(rates):
            _warn(issues, '%s[%s]' % (path, location),
                  '分段报销比例不是随费用递增（%s），请确认是否符合政策' % rates)


def _validate_cities(cities: Mapping[str, Any], issues: List[Issue]) -> None:
    if not isinstance(cities, Mapping) or not cities:
        _err(issues, 'cities', '城市政策必须是非空对象')
        return

    for city_code, city in cities.items():
        cpath = 'cities.%s' % city_code
        if not isinstance(city, Mapping):
            _err(issues, cpath, '城市政策必须是对象')
            continue
        if not city.get('name'):
            _err(issues, cpath + '.name', '缺少城市名称')

        insurances = [code for code in INSURANCE_TYPE_CODES if code in city]
        if not insurances:
            _err(issues, cpath, '至少需要一种医保类型（%s）' % ' / '.join(INSURANCE_TYPE_CODES))
            continue

        for insurance in insurances:
            visits = city[insurance]
            if not isinstance(visits, Mapping) or not visits:
                _err(issues, '%s.%s' % (cpath, insurance), '就医类型政策不能为空')
                continue
            for visit_type, policy in visits.items():
                vpath = '%s.%s.%s' % (cpath, insurance, visit_type)
                if not isinstance(policy, Mapping):
                    _err(issues, vpath, '政策必须是对象')
                    continue

                deductible = policy.get('deductible')
                if not _is_number(deductible) or deductible < 0:
                    _err(issues, vpath + '.deductible', '起付线必须是非负数，当前为 %r' % (deductible,))

                cap = policy.get('max_reimburse')
                if not _is_number(cap) or cap <= 0:
                    _err(issues, vpath + '.max_reimburse', '封顶线必须是正数，当前为 %r' % (cap,))

                tiers = policy.get('reimburse_rates')
                if not isinstance(tiers, list) or not tiers:
                    _err(issues, vpath + '.reimburse_rates', '报销比例分段不能为空')
                else:
                    _validate_tiers(tiers, vpath + '.reimburse_rates', issues)

                default_level = policy.get('default_hospital_level')
                if not default_level:
                    _warn(issues, vpath + '.default_hospital_level', '未设置默认医院等级')
                elif isinstance(tiers, list) and default_level not in {
                    t.get('location') for t in tiers if isinstance(t, Mapping)
                }:
                    _err(issues, vpath + '.default_hospital_level',
                         '默认医院等级 %r 在报销比例分段中不存在' % default_level)

                if _is_number(deductible) and _is_number(cap) and deductible >= cap:
                    _warn(issues, vpath, '起付线(%s)不小于封顶线(%s)，该政策永远无法报销' % (deductible, cap))


def _validate_extended(extended: Mapping[str, Any], cities: Mapping[str, Any],
                       issues: List[Issue]) -> None:
    if not isinstance(extended, Mapping):
        _err(issues, 'extended', '扩展政策数据必须是对象')
        return

    # -- 门诊慢特病 --------------------------------------------------------
    chronic = extended.get('chronic_diseases') or {}
    diseases = chronic.get('diseases') or {}
    if chronic.get('enabled') and not diseases:
        _err(issues, 'extended.chronic_diseases.diseases', '启用慢特病但未定义病种')
    for code, disease in diseases.items():
        dpath = 'extended.chronic_diseases.diseases.%s' % code
        if not (disease or {}).get('name'):
            _err(issues, dpath + '.name', '缺少病种名称')
        buckets = [('default', disease.get('default') or {})]
        buckets += [('cities.%s' % c, v or {}) for c, v in (disease.get('cities') or {}).items()]
        for label, bucket in buckets:
            bpath = '%s.%s' % (dpath, label)
            rate = bucket.get('rate')
            if rate is not None and (not _is_number(rate) or not 0 <= rate <= 1):
                _err(issues, bpath + '.rate', '报销比例必须为 0~1，当前为 %r' % (rate,))
            quota = bucket.get('annual_quota')
            if quota is not None and (not _is_number(quota) or quota <= 0):
                _err(issues, bpath + '.annual_quota', '年度限额必须是正数，当前为 %r' % (quota,))
            ded = bucket.get('deductible')
            if ded is not None and (not _is_number(ded) or ded < 0):
                _err(issues, bpath + '.deductible', '起付线必须是非负数，当前为 %r' % (ded,))
        for city_code in (disease.get('cities') or {}):
            if city_code not in cities:
                _err(issues, '%s.cities.%s' % (dpath, city_code), '覆盖了不存在的城市')

    # -- 个人账户 ----------------------------------------------------------
    account = extended.get('personal_account') or {}
    active = ((account.get('employee') or {}).get('active') or {})
    rate = active.get('personal_rate')
    if rate is not None and (not _is_number(rate) or not 0 <= rate <= 1):
        _err(issues, 'extended.personal_account.employee.active.personal_rate',
             '计入比例必须为 0~1，当前为 %r' % (rate,))
    employer_rate = active.get('employer_rate_to_account')
    if employer_rate is not None and (not _is_number(employer_rate) or not 0 <= employer_rate <= 1):
        _err(issues, 'extended.personal_account.employee.active.employer_rate_to_account',
             '单位划入比例必须为 0~1，当前为 %r' % (employer_rate,))

    # -- 缴费 --------------------------------------------------------------
    contribution = extended.get('contribution') or {}
    for section in ('employee', 'flexible_employment'):
        block = contribution.get(section) or {}
        for key, value in block.items():
            if not key.endswith('rate'):
                continue
            if not _is_number(value) or not 0 <= value <= 1:
                _err(issues, 'extended.contribution.%s.%s' % (section, key),
                     '费率必须为 0~1，当前为 %r' % (value,))
    for city_code, limits in (contribution.get('base_limits') or {}).items():
        if city_code not in cities:
            _err(issues, 'extended.contribution.base_limits.%s' % city_code, '覆盖了不存在的城市')
        low, high = (limits or {}).get('min'), (limits or {}).get('max')
        if not _is_number(low) or not _is_number(high) or low <= 0 or high <= low:
            _err(issues, 'extended.contribution.base_limits.%s' % city_code,
                 '缴费基数上下限非法：min=%r max=%r' % (low, high))

    # -- 生育 --------------------------------------------------------------
    maternity = extended.get('maternity') or {}
    items = ((maternity.get('medical') or {}).get('items') or {})
    for code, item in items.items():
        limit = (item or {}).get('limit')
        if not _is_number(limit) or limit <= 0:
            _err(issues, 'extended.maternity.medical.items.%s.limit' % code,
                 '报销限额必须是正数，当前为 %r' % (limit,))
        item_rate = (item or {}).get('rate', 1.0)
        if not _is_number(item_rate) or not 0 <= item_rate <= 1:
            _err(issues, 'extended.maternity.medical.items.%s.rate' % code,
                 '报销比例必须为 0~1，当前为 %r' % (item_rate,))
    for city_code, overrides in ((maternity.get('medical') or {}).get('city_overrides') or {}).items():
        if city_code not in cities:
            _err(issues, 'extended.maternity.medical.city_overrides.%s' % city_code,
                 '覆盖了不存在的城市')
        for item_code in overrides:
            if item_code not in items:
                _err(issues, 'extended.maternity.medical.city_overrides.%s.%s' % (city_code, item_code),
                     '覆盖了未定义的生育项目')

    # -- 家庭共济 ----------------------------------------------------------
    household = extended.get('household') or {}
    if household.get('enabled'):
        if not household.get('member_relations'):
            _err(issues, 'extended.household.member_relations', '启用共济但未定义亲属关系')
        if not household.get('allowed_usage'):
            _err(issues, 'extended.household.allowed_usage', '启用共济但未定义可用范围')

    # -- 多次住院起付线递减 ------------------------------------------------
    multi = extended.get('multi_admission') or {}
    if multi.get('enabled'):
        buckets = [('default', multi.get('default') or {})]
        buckets += [('city_overrides.%s' % c, v or {})
                    for c, v in (multi.get('city_overrides') or {}).items()]
        for label, bucket in buckets:
            bpath = 'extended.multi_admission.%s' % label
            factors = bucket.get('deductible_factors')
            if not factors:
                _err(issues, bpath + '.deductible_factors', '必须给出住院起付线递减系数序列')
                continue
            for index, factor in enumerate(factors):
                if not _is_number(factor) or not 0 <= factor <= 1:
                    _err(issues, '%s.deductible_factors[%d]' % (bpath, index),
                         '递减系数必须为 0~1，当前为 %r' % (factor,))
            if factors != sorted(factors, reverse=True):
                _warn(issues, bpath + '.deductible_factors',
                      '递减系数不是单调不增（%s），请确认规则方向' % factors)
        for city_code in (multi.get('city_overrides') or {}):
            if city_code not in cities:
                _err(issues, 'extended.multi_admission.city_overrides.%s' % city_code,
                     '覆盖了不存在的城市')


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def validate_policy_bundle(base, cities, extended, source: str = '<memory>') -> Dict[str, Any]:
    """校验一整套政策数据。

    Args:
        base: ``base.json`` 内容
        cities: ``cities.json`` 内容（含 ``cities`` 键）
        extended: ``extended.json`` 内容
        source: 出错时用于定位的数据来源描述

    Returns:
        ``{'ok': bool, 'source': str, 'errors': [Issue, ...], 'warnings': [Issue, ...]}``
    """
    issues: List[Issue] = []

    city_map = (cities or {}).get('cities') if isinstance(cities, Mapping) else None
    if city_map is None:
        _err(issues, 'cities', '缺少顶层 cities 字段')
        city_map = {}

    _validate_base(base, issues)
    _validate_cities(city_map, issues)
    _validate_extended(extended, city_map, issues)

    errors = [i for i in issues if i['level'] == 'error']
    report = {
        'ok': not errors,
        'source': source,
        'errors': errors,
        'warnings': [i for i in issues if i['level'] == 'warning'],
    }
    return report


def assert_valid(report: Mapping[str, Any]) -> None:
    """报告中有 error 时抛出 :class:`PolicyValidationError`。"""
    if report.get('errors'):
        raise PolicyValidationError(report['errors'], source=report.get('source', '<unknown>'))
