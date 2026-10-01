"""harness-version-management 1.1 — the harness registry pin reader.

Pins the exact-pin field, the read-only reader (deploy 1.2 + plugin 1.3 consume it),
and that an absent pin is the honest unpinned state, not an error.
"""

from __future__ import annotations

import pytest

from otaman_core.harness_registry import (
    Harness,
    HarnessRegistryError,
    find_harness,
    harness_pin,
    read_harness_registry,
)

_CONFIG = {
    "runner": {
        "harnesses": [
            {"id": "claude-code", "binary": "claude", "pin": "1.2.3"},
            {"id": "legacy", "binary": "old", "min_version": "0.9"},  # pre-pin, unpinned
        ]
    }
}


def test_reads_entries():
    reg = read_harness_registry(_CONFIG)
    assert [h.id for h in reg] == ["claude-code", "legacy"]
    assert reg[0] == Harness(id="claude-code", binary="claude", pin="1.2.3")


def test_pinned_property():
    reg = read_harness_registry(_CONFIG)
    assert reg[0].pinned is True
    assert reg[1].pinned is False  # only min_version, no exact pin


def test_harness_pin_lookup():
    assert harness_pin(_CONFIG, "claude-code") == "1.2.3"
    assert harness_pin(_CONFIG, "legacy") is None  # unpinned → None, not an error
    assert harness_pin(_CONFIG, "nonexistent") is None


def test_find_harness():
    assert find_harness(_CONFIG, "claude-code").binary == "claude"
    assert find_harness(_CONFIG, "nope") is None


def test_absent_runner_or_harnesses_is_empty():
    assert read_harness_registry({}) == ()
    assert read_harness_registry({"runner": {}}) == ()
    assert read_harness_registry({"runner": {"harnesses": []}}) == ()
    assert harness_pin({}, "claude-code") is None


@pytest.mark.parametrize(
    "config",
    [
        {"runner": "nope"},  # runner not a mapping
        {"runner": {"harnesses": "nope"}},  # harnesses not a list
        {"runner": {"harnesses": [{"binary": "x"}]}},  # entry missing id
        {"runner": {"harnesses": [{"id": ""}]}},  # empty id
        {"runner": {"harnesses": ["claude"]}},  # entry not a mapping
        {"runner": {"harnesses": [{"id": "x", "pin": 123}]}},  # pin not a string
    ],
)
def test_rejects_malformed(config):
    with pytest.raises(HarnessRegistryError):
        read_harness_registry(config)
