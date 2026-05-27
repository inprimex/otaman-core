"""Tests for otaman_core.validate_message and .otaman marker agent field."""

from __future__ import annotations
from pathlib import Path
import pytest
from otaman_core.validate_message import validate_message


def _write_msg(tmp_path, frontmatter, body="## Subject: test\n"):
    p = tmp_path / "msg.md"
    p.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return p

def _valid_fm(**overrides):
    fields = {
        "id": "20260528T120000-abc",
        "from": "core-agent",
        "to": "human",
        "type": "info",
        "timestamp": "2026-05-28T12:00:00Z",
        "priority": "normal",
    }
    fields.update(overrides)
    return "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)


class TestRequiredFields:
    def test_valid_minimal_message(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm())) == []

    def test_missing_id(self, tmp_path):
        fm = "\n".join(l for l in _valid_fm().splitlines() if not l.startswith("id:"))
        assert any("id" in e for e in validate_message(_write_msg(tmp_path, fm)))

    def test_missing_to(self, tmp_path):
        fm = "\n".join(l for l in _valid_fm().splitlines() if not l.startswith("to:"))
        assert any("to" in e for e in validate_message(_write_msg(tmp_path, fm)))

    def test_missing_subject_line(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(), body="no subject\n")
        assert any("Subject" in e for e in validate_message(p))

    def test_unknown_type(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(type="not-a-type"))
        assert any("Unknown type" in e or "type" in e.lower() for e in validate_message(p))

    def test_unknown_priority(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(priority="critical"))
        assert any("priority" in e.lower() for e in validate_message(p))


class TestReplyTo:
    def test_valid_agent(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(**{"reply-to": "runner-agent"}))) == []

    def test_valid_human(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(**{"reply-to": "human"}))) == []

    def test_absent_ok(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm())) == []

    def test_invalid_bare_name(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(**{"reply-to": "Romans Laptop"}))
        assert any("reply-to" in e for e in validate_message(p))

    def test_invalid_email(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(**{"reply-to": "foo@bar.com"}))
        assert any("reply-to" in e for e in validate_message(p))


class TestToField:
    KNOWN = {"core-agent", "cli-agent", "bridge-agent"}

    def test_to_all_broadcast(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="all", type="contract-change"))
        assert validate_message(p) == []

    def test_to_human(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(to="human"))) == []

    def test_single_known_agent(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(to="core-agent")), self.KNOWN) == []

    def test_comma_list_known(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(to="core-agent, cli-agent")), self.KNOWN) == []

    def test_comma_list_unknown_flagged(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="core-agent, ghost-agent"))
        assert any("ghost-agent" in e for e in validate_message(p, self.KNOWN))

    def test_single_unknown_flagged(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="ghost-agent"))
        assert any("ghost-agent" in e for e in validate_message(p, self.KNOWN))


class TestBroadcastWhitelist:
    def test_contract_change_all_ok(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="contract-change"))) == []

    def test_emergency_halt_all_ok(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="emergency-halt"))) == []

    def test_agent_registry_change_all_ok(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="agent-registry-change"))) == []

    def test_info_all_invalid(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="all", type="info"))
        assert any("all" in e for e in validate_message(p))

    def test_task_assignment_all_invalid(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="all", type="task-assignment"))
        assert any("all" in e for e in validate_message(p))

    def test_task_complete_all_invalid(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="all", type="task-complete"))
        assert any("all" in e for e in validate_message(p))

    def test_targeted_info_ok(self, tmp_path):
        assert validate_message(_write_msg(tmp_path, _valid_fm(to="core-agent", type="info"))) == []


class TestMarkerAgentField:
    def test_agent_field_parsed(self, tmp_path):
        from otaman_core._resolve import parse_marker_fields
        m = tmp_path / ".otaman"
        m.write_text("otaman_root: ../meta\nagent: core-agent\n")
        assert parse_marker_fields(m)["agent"] == "core-agent"

    def test_agent_field_absent_ok(self, tmp_path):
        from otaman_core._resolve import parse_marker_fields
        m = tmp_path / ".otaman"
        m.write_text("otaman_root: ../meta\n")
        assert "agent" not in parse_marker_fields(m)

    def test_agent_does_not_affect_root(self, tmp_path):
        from otaman_core._resolve import parse_marker_fields
        m = tmp_path / ".otaman"
        m.write_text("otaman_root: ../meta\nagent: bridge-agent\n")
        f = parse_marker_fields(m)
        assert f["otaman_root"] == "../meta"
        assert f["agent"] == "bridge-agent"

    def test_unknown_fields_ignored(self, tmp_path):
        from otaman_core._resolve import parse_marker_fields
        m = tmp_path / ".otaman"
        m.write_text("otaman_root: ../meta\nfuture_field: value\n")
        assert "future_field" not in parse_marker_fields(m)
