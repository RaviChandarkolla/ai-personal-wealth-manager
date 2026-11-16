"""Unit tests for rule catalog, live example generation, and coverage (domain/rule_catalog.py)."""

import pytest
from wealth_advisor.config import Settings, get_settings
from wealth_advisor.domain.rule_catalog import (
    RULE_CATALOG,
    compare_rule_sets,
    describe_rule_set,
)
from wealth_advisor.domain.rulesets import SET1_RULES, SET2_RULES, use_rule_set


def test_catalog_entries_setting_keys_exist_in_settings():
    """Every setting_key declared in any rule catalog entry must exist as a field on Settings."""
    settings_fields = set(Settings.model_fields.keys())
    for entry in RULE_CATALOG:
        for k in entry.setting_keys:
            assert k in settings_fields, (
                f"Rule {entry.id} references setting key {k!r} which is not a field on Settings"
            )


def test_catalog_examples_build_for_all_rule_sets():
    """Ensure example_fn and value_fn execute without raising errors under set1, set2, and combined."""
    for r_set in ("set1", "set2", "combined"):
        with use_rule_set(r_set):
            s = get_settings(r_set)
            for entry in RULE_CATALOG:
                ex = entry.example_fn(s)
                assert isinstance(ex, str) and len(ex) > 0, (
                    f"Example for rule {entry.id} failed under {r_set}"
                )
                val = entry.value_fn(s)
                assert val is not None, f"value_fn for rule {entry.id} returned None under {r_set}"


def test_catalog_covers_all_rule_set_overrides():
    """Every setting that Set 1 or Set 2 overrides should be represented in at least one catalog entry."""
    catalog_keys = {k for entry in RULE_CATALOG for k in entry.setting_keys}
    ignored_keys = {"fund_rule_set"}  # internal meta key

    set1_keys = set(SET1_RULES.keys()) - ignored_keys
    set2_keys = set(SET2_RULES.keys()) - ignored_keys
    all_override_keys = set1_keys | set2_keys

    missing = all_override_keys - catalog_keys
    assert not missing, f"Rule catalog is missing entries covering settings: {missing}"


def test_describe_rule_set_returns_grouped_rules_and_provenance():
    """describe_rule_set returns groups, rules with values/examples, and provenance for combined."""
    desc_s1 = describe_rule_set("set1")
    assert desc_s1["rule_set"] == "set1"
    assert "groups" in desc_s1
    group_names = [g["name"] for g in desc_s1["groups"]]
    assert "Allocation" in group_names

    desc_comb = describe_rule_set("combined")
    assert desc_comb["rule_set"] == "combined"
    assert "provenance" in desc_comb
    assert len(desc_comb["provenance"]) > 0



def test_compare_rule_sets_tabular_structure():
    """compare_rule_sets returns rows with set1, set2, combined values, strategy and winner."""
    rows = compare_rule_sets()
    assert len(rows) >= len(RULE_CATALOG)
    for row in rows:
        assert "rule_id" in row
        assert "title" in row
        assert "set1" in row
        assert "set2" in row
        assert "combined" in row
        assert "strategy" in row
