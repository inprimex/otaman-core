"""Tests for otaman_core.lifecycle — program lifecycle registry (1.1) + doctor (1.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    pytest.skip("PyYAML not installed", allow_module_level=True)

from otaman_core.lifecycle import (
    DEFAULT_STATE,
    LIFECYCLE_STATES,
    DoctorFinding,
    LifecycleEntry,
    LifecycleError,
    check_lifecycle,
    get_state,
    lifecycle_registry_path,
    load_lifecycle,
    parse_lifecycle,
    read_program_state,
    record_transition,
)

FIXED = lambda: "2026-08-29T09:00:00+00:00"  # noqa: E731


def _write_registry(org_root: Path, mapping: dict) -> Path:
    path = lifecycle_registry_path(org_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"programs": mapping}), encoding="utf-8")
    return path


class TestParseAndLoad:
    def test_path_is_org_config_lifecycle(self, tmp_path):
        assert lifecycle_registry_path(tmp_path) == tmp_path / "config" / "lifecycle.yaml"

    def test_none_and_empty_yield_empty(self):
        assert parse_lifecycle(None) == {}
        assert parse_lifecycle({}) == {}
        assert parse_lifecycle({"programs": {}}) == {}

    def test_parses_entry_with_history(self):
        reg = parse_lifecycle(
            {
                "programs": {
                    "alpha": {
                        "state": "suspended",
                        "since": "2026-08-29T09:00:00+00:00",
                        "by": "Ana",
                        "reason": "maintenance",
                        "history": [
                            {"state": "active", "since": "t0", "by": "Ana"},
                            {
                                "state": "suspended",
                                "since": "t1",
                                "by": "Ana",
                                "reason": "maintenance",
                            },
                        ],
                    }
                }
            }
        )
        e = reg["alpha"]
        assert e.state == "suspended" and e.by == "Ana" and e.reason == "maintenance"
        assert len(e.history) == 2 and e.history[-1].state == "suspended"

    def test_unknown_state_raises(self):
        with pytest.raises(LifecycleError, match="state"):
            parse_lifecycle({"programs": {"a": {"state": "frozen"}}})

    def test_non_mapping_raises(self):
        with pytest.raises(LifecycleError):
            parse_lifecycle(["not", "a", "map"])
        with pytest.raises(LifecycleError, match="programs"):
            parse_lifecycle({"programs": ["x"]})

    def test_history_not_list_raises(self):
        with pytest.raises(LifecycleError, match="history"):
            parse_lifecycle({"programs": {"a": {"state": "active", "history": "x"}}})

    def test_load_absent_file_is_empty(self, tmp_path):
        assert load_lifecycle(lifecycle_registry_path(tmp_path)) == {}

    def test_load_corrupt_yaml_is_empty(self, tmp_path):
        path = lifecycle_registry_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("not: valid: [yaml")
        assert load_lifecycle(path) == {}

    def test_load_structural_error_raises(self, tmp_path):
        path = _write_registry(tmp_path, {"a": {"state": "frozen"}})
        with pytest.raises(LifecycleError):
            load_lifecycle(path)


class TestReadHelper:
    def test_get_state_absent_is_active(self):
        assert get_state({}, "nope") == DEFAULT_STATE == "active"

    def test_get_state_present(self):
        reg = parse_lifecycle({"programs": {"a": {"state": "limited"}}})
        assert get_state(reg, "a") == "limited"

    def test_read_program_state_absent_registry_is_active(self, tmp_path):
        # Scenario: absent registry means active.
        assert read_program_state(tmp_path, "anything") == "active"

    def test_read_program_state_reads_current(self, tmp_path):
        _write_registry(tmp_path, {"alpha": {"state": "archived", "since": "t", "by": "Ana"}})
        assert read_program_state(tmp_path, "alpha") == "archived"
        assert read_program_state(tmp_path, "other") == "active"  # absent entry


class TestRecordTransition:
    def test_creates_file_and_appends_history(self, tmp_path):
        path = lifecycle_registry_path(tmp_path)
        entry = record_transition(path, "alpha", "limited", by="Ana", reason="q4", now=FIXED)
        assert entry.state == "limited" and entry.by == "Ana" and entry.reason == "q4"
        assert entry.since == "2026-08-29T09:00:00+00:00"
        assert len(entry.history) == 1
        # persisted + re-readable
        assert read_program_state(tmp_path, "alpha") == "limited"

    def test_history_accumulates_across_transitions(self, tmp_path):
        path = lifecycle_registry_path(tmp_path)
        record_transition(path, "alpha", "limited", by="Ana", now=FIXED)
        record_transition(path, "alpha", "suspended", by="Bob", reason="incident", now=FIXED)
        entry = load_lifecycle(path)["alpha"]
        assert [r.state for r in entry.history] == ["limited", "suspended"]
        assert entry.state == "suspended" and entry.by == "Bob"

    def test_other_programs_preserved(self, tmp_path):
        path = lifecycle_registry_path(tmp_path)
        record_transition(path, "alpha", "limited", by="Ana", now=FIXED)
        record_transition(path, "beta", "archived", by="Ana", now=FIXED)
        reg = load_lifecycle(path)
        assert get_state(reg, "alpha") == "limited"
        assert get_state(reg, "beta") == "archived"

    def test_unknown_state_raises(self, tmp_path):
        with pytest.raises(LifecycleError, match="state"):
            record_transition(lifecycle_registry_path(tmp_path), "a", "frozen", by="Ana")

    def test_empty_by_raises(self, tmp_path):
        with pytest.raises(LifecycleError, match="by"):
            record_transition(lifecycle_registry_path(tmp_path), "a", "limited", by="  ")

    def test_state_survives_archival_readback(self, tmp_path):
        # Scenario: archived state (with since/by) stays present + readable.
        path = lifecycle_registry_path(tmp_path)
        record_transition(path, "alpha", "archived", by="Ana", reason="eol", now=FIXED)
        entry = load_lifecycle(path)["alpha"]
        assert entry.state == "archived" and entry.since and entry.by == "Ana"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    def test_written_0644(self, tmp_path):
        path = lifecycle_registry_path(tmp_path)
        record_transition(path, "alpha", "limited", by="Ana", now=FIXED)
        assert (path.stat().st_mode & 0o777) == 0o644


class TestDoctor:
    def test_live_session_in_non_active_program_warns(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "suspended"}}})
        findings = check_lifecycle(reg, live_programs=["alpha"])
        assert [f.level for f in findings] == ["warn"]
        assert "suspended" in findings[0].message and "alpha" in findings[0].message

    def test_live_session_in_active_program_ok(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "active"}}})
        assert check_lifecycle(reg, live_programs=["alpha"]) == []

    def test_live_session_in_absent_program_ok(self):
        # absent entry == active -> not flagged
        assert check_lifecycle({}, live_programs=["ghost"]) == []

    def test_archived_but_present_warns(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "archived"}}})
        findings = check_lifecycle(reg, folder_present={"alpha": True})
        assert [f.level for f in findings] == ["warn"]
        assert "archived" in findings[0].message and "still present" in findings[0].message

    def test_non_archived_but_missing_warns(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "limited"}}})
        findings = check_lifecycle(reg, folder_present={"alpha": False})
        assert [f.level for f in findings] == ["warn"]
        assert "missing" in findings[0].message

    def test_archived_and_absent_folder_ok(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "archived"}}})
        assert check_lifecycle(reg, folder_present={"alpha": False}) == []

    def test_active_and_present_folder_ok(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "active"}}})
        assert check_lifecycle(reg, folder_present={"alpha": True}) == []

    def test_folder_present_none_skips_folder_checks(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "archived"}}})
        assert check_lifecycle(reg, folder_present=None) == []

    def test_unknown_folder_presence_skipped(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "archived"}}})
        # program not in folder_present map -> no folder finding
        assert check_lifecycle(reg, folder_present={}) == []

    def test_findings_are_doctor_findings(self):
        reg = parse_lifecycle({"programs": {"alpha": {"state": "suspended"}}})
        findings = check_lifecycle(reg, live_programs=["alpha"])
        assert isinstance(findings[0], DoctorFinding)


def test_lifecycle_states_constant():
    assert LIFECYCLE_STATES == ("active", "limited", "suspended", "archived")
    assert isinstance(LifecycleEntry, type)
