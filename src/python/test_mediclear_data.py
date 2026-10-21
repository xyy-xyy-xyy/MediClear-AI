# -*- coding: utf-8 -*-
"""数据层单元测试：政策仓库、多版本管理、数据校验。

数据层的目标是**政策变动不改代码**——加一个城市、切一个年份、
校验一份新数据，都应该只动 JSON。
"""

import json
import os
import shutil

import pytest

import rules
from mediclear.data import PolicyRepository, clone_bundle, get_repository
from mediclear.data.repository import PolicyBundle
from mediclear.data.schema import assert_valid, validate_policy_bundle
from mediclear.exceptions import PolicyDataError, PolicyValidationError, VersionNotFoundError


@pytest.fixture(autouse=True)
def _restore_default_year():
    """每个用例跑完后把全局默认年份还原，避免互相污染。"""
    repository = get_repository()
    original = repository.default_year
    yield
    repository.use_version(original)


# --------------------------------------------------------------------------
# 版本管理
# --------------------------------------------------------------------------
def test_repository_lists_both_versions():
    versions = get_repository().list_versions()
    years = [v['year'] for v in versions]
    assert 2025 in years and 2026 in years
    assert get_repository().default_year == 2026


def test_use_version_switches_data():
    repository = get_repository()
    bundle_2026 = repository.use_version(2026)
    bundled_rate = bundle_2026.get_visit_policy('beijing', 'employee', 'inpatient')
    rate_2026 = bundled_rate['reimburse_rates'][0]['rate']

    bundle_2025 = repository.use_version(2025)
    rate_2025 = bundle_2025.get_visit_policy(
        'beijing', 'employee', 'inpatient')['reimburse_rates'][0]['rate']

    assert rate_2025 < rate_2026  # 2025 年比例低 1 个百分点


def test_unknown_version_raises():
    with pytest.raises(VersionNotFoundError):
        get_repository().load(1999)


def test_bundle_is_cached_per_year():
    """同一版本应命中缓存，而不是每次读盘。"""
    repository = get_repository()
    assert repository.load(2026) is repository.load(2026)


def test_clone_bundle_is_independent():
    bundle = get_repository().current
    copy = clone_bundle(bundle)
    copy.cities['beijing']['employee']['inpatient']['deductible'] = 9999
    assert bundle.cities['beijing']['employee']['inpatient']['deductible'] != 9999


def test_repository_accepts_custom_root(tmp_path):
    """可以把仓库指向别处（多套政策并存 / 测试替身）。"""
    source = os.path.join(os.path.dirname(get_repository().root), '')
    shutil.copytree(get_repository().root, str(tmp_path / 'policies'))
    custom = PolicyRepository(root=str(tmp_path / 'policies'))
    assert custom.default_year == 2026
    assert 'beijing' in custom.current.cities
    assert source  # 仅用于表达"源目录存在"，避免未使用变量告警


# --------------------------------------------------------------------------
# 数据校验
# --------------------------------------------------------------------------
def test_shipped_policies_validate_cleanly():
    """仓库里自带的两年政策都应 0 错误。"""
    for year in (2025, 2026):
        report = get_repository().validate(year)
        assert report['ok'] is True, report['errors']
        assert report['errors'] == []


def test_schema_catches_missing_cities_file():
    report = validate_policy_bundle({'insurance_types': {}}, {}, {}, 'bad-source')
    assert report['ok'] is False
    paths = {e['path'] for e in report['errors']}
    assert 'cities' in paths


def test_schema_catches_non_contiguous_tiers():
    """分段表必须从 0 开始且首尾相接，否则计算会漏掉一整段费用。"""
    base = {
        'insurance_types': {'employee': {'name': '职工', 'visit_types': ['inpatient']},
                            'resident': {'name': '居民', 'visit_types': ['inpatient']}},
        'visit_types': {'inpatient': {'name': '住院'}},
        'fee_categories': {'class_a': {'name': '甲类', 'reimburse_ratio_base': 1.0}},
        'hospital_levels': ['三级医院'],
        'serious_illness': {'enabled': False},
        'relocation': {},
    }
    cities = {'cities': {'x': {'name': 'X市', 'employee': {'inpatient': {
        'deductible': 100, 'max_reimburse': 10000, 'default_hospital_level': '三级医院',
        'reimburse_rates': [
            {'min': 0, 'max': 1000, 'rate': 0.8, 'location': '三级医院'},
            # 与上一段不连续：1000 → 5000，中间 4000 元无政策
            {'min': 5000, 'max': 10000, 'rate': 0.9, 'location': '三级医院'},
        ]}}}}}
    report = validate_policy_bundle(base, cities, {}, 'gap-source')
    assert report['ok'] is False
    assert any('连续' in e['message'] or '衔接' in e['message']
               for e in report['errors']), report['errors']


def test_schema_catches_out_of_range_rate():
    base = {
        'insurance_types': {'employee': {'name': '职工', 'visit_types': ['inpatient']},
                            'resident': {'name': '居民', 'visit_types': ['inpatient']}},
        'visit_types': {'inpatient': {'name': '住院'}},
        'fee_categories': {'class_a': {'name': '甲类', 'reimburse_ratio_base': 1.0}},
        'hospital_levels': ['三级医院'],
        'serious_illness': {'enabled': False},
        'relocation': {},
    }
    cities = {'cities': {'x': {'name': 'X市', 'employee': {'inpatient': {
        'deductible': 100, 'max_reimburse': 10000, 'default_hospital_level': '三级医院',
        'reimburse_rates': [
            {'min': 0, 'max': 10000, 'rate': 1.5, 'location': '三级医院'},
        ]}}}}}
    report = validate_policy_bundle(base, cities, {}, 'rate-source')
    assert report['ok'] is False


def test_assert_valid_raises_with_error_list():
    report = validate_policy_bundle({}, {}, {}, 'empty')
    with pytest.raises(PolicyValidationError) as exc:
        assert_valid(report)
    assert exc.value.errors
    assert isinstance(exc.value, PolicyDataError)


# --------------------------------------------------------------------------
# 兼容层 rules.py
# --------------------------------------------------------------------------
def test_rules_module_exports_legacy_constants():
    assert 'employee' in rules.INSURANCE_TYPES
    assert 'inpatient' in rules.VISIT_TYPES
    assert 'class_a' in rules.FEE_CATEGORIES
    assert rules.SERIOUS_ILLNESS_INSURANCE
    assert rules.RELOCATION_POLICY


def test_rules_query_functions():
    cities = rules.get_supported_cities()
    assert len(cities) >= 10
    assert all({'code', 'name', 'province', 'tier'} <= set(c) for c in cities)
    assert rules.get_city_policy('beijing', 'employee') is not None
    assert rules.get_city_policy('atlantis', 'employee') is None
    assert rules.get_visit_type_policy('beijing', 'employee', 'inpatient') is not None
    assert rules.get_cities_by_tier('一线')


def test_rules_set_policy_year_rebinds_globals():
    """切年份后，模块级常量必须跟着变（否则老代码会读到旧数据）。"""
    try:
        rules.set_policy_year(2025)
        assert rules.CITY_POLICIES['beijing']['employee']['inpatient']['deductible'] is not None
        first_2025 = rules.CITY_POLICIES['beijing']['employee']['inpatient']['reimburse_rates'][0]['rate']
        rules.set_policy_year(2026)
        first_2026 = rules.CITY_POLICIES['beijing']['employee']['inpatient']['reimburse_rates'][0]['rate']
        assert first_2025 < first_2026
    finally:
        rules.set_policy_year(2026)


def test_rules_validate_policies_reports_ok():
    report = rules.validate_policies(2026)
    assert report['ok'] is True


# --------------------------------------------------------------------------
# 数据完整性（跨文件引用）
# --------------------------------------------------------------------------
def test_extended_data_covers_every_city():
    """慢特病 / 缴费基数 / 生育 等扩展政策必须覆盖全部城市。"""
    bundle = get_repository().current
    city_codes = set(bundle.cities)

    limits = bundle.contribution.get('base_limits') or {}
    assert city_codes <= set(limits), '缴费基数缺少城市：%s' % (city_codes - set(limits))

    for city in city_codes:
        assert bundle.resolve_chronic_disease('hypertension', city)['annual_quota'] > 0
        assert bundle.get_visit_policy(city, 'employee', 'inpatient') is not None


def test_policy_json_is_valid_utf8_and_parsable():
    bundle = get_repository().current
    for name in ('base', 'cities', 'extended'):
        path = os.path.join(bundle.source_dir, '%s.json' % name)
        with open(path, encoding='utf-8') as handle:
            assert isinstance(json.load(handle), dict)
