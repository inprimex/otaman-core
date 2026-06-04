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


def _errors(tmp_path, frontmatter, body="## Subject: test\n"):
    errors, _ = validate_message(_write_msg(tmp_path, frontmatter, body))
    return errors

def _warnings(tmp_path, frontmatter, body="## Subject: test\n"):
    _, warnings = validate_message(_write_msg(tmp_path, frontmatter, body))
    return warnings


class TestRequiredFields:
    def test_valid_minimal_message(self, tmp_path):
        errors, warnings = validate_message(_write_msg(tmp_path, _valid_fm()))
        assert errors == []
        assert warnings == []

    def test_missing_id(self, tmp_path):
        fm = "\n".join(l for l in _valid_fm().splitlines() if not l.startswith("id:"))
        assert any("id" in e for e in _errors(tmp_path, fm))

    def test_missing_to(self, tmp_path):
        fm = "\n".join(l for l in _valid_fm().splitlines() if not l.startswith("to:"))
        assert any("to" in e for e in _errors(tmp_path, fm))

    def test_missing_subject_line(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(), body="no subject\n")
        errors, _ = validate_message(p)
        assert any("Subject" in e for e in errors)

    def test_unknown_type(self, tmp_path):
        assert any(
            "Unknown type" in e or "type" in e.lower()
            for e in _errors(tmp_path, _valid_fm(type="not-a-type"))
        )

    def test_unknown_priority(self, tmp_path):
        assert any("priority" in e.lower() for e in _errors(tmp_path, _valid_fm(priority="critical")))


class TestReplyTo:
    def test_valid_agent(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(**{"reply-to": "runner-agent"})))
        assert errors == []

    def test_valid_human(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(**{"reply-to": "human"})))
        assert errors == []

    def test_absent_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm()))
        assert errors == []

    def test_invalid_bare_name(self, tmp_path):
        assert any("reply-to" in e for e in _errors(tmp_path, _valid_fm(**{"reply-to": "Romans Laptop"})))

    def test_invalid_email(self, tmp_path):
        assert any("reply-to" in e for e in _errors(tmp_path, _valid_fm(**{"reply-to": "foo@bar.com"})))


class TestToField:
    KNOWN = {"core-agent", "cli-agent", "bridge-agent"}

    def test_to_all_broadcast(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="contract-change")))
        assert errors == []

    def test_to_human(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="human")))
        assert errors == []

    def test_single_known_agent(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="core-agent")), self.KNOWN)
        assert errors == []

    def test_comma_list_known(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="core-agent, cli-agent")), self.KNOWN)
        assert errors == []

    def test_comma_list_unknown_flagged(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="core-agent, ghost-agent"))
        errors, _ = validate_message(p, self.KNOWN)
        assert any("ghost-agent" in e for e in errors)

    def test_single_unknown_flagged(self, tmp_path):
        p = _write_msg(tmp_path, _valid_fm(to="ghost-agent"))
        errors, _ = validate_message(p, self.KNOWN)
        assert any("ghost-agent" in e for e in errors)


class TestBroadcastWhitelist:
    def test_contract_change_all_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="contract-change")))
        assert errors == []

    def test_emergency_halt_all_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="emergency-halt")))
        assert errors == []

    def test_agent_registry_change_all_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="agent-registry-change")))
        assert errors == []

    def test_info_all_invalid(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="info")))
        assert any("all" in e for e in errors)

    def test_task_assignment_all_invalid(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="task-assignment")))
        assert any("all" in e for e in errors)

    def test_task_complete_all_invalid(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="all", type="task-complete")))
        assert any("all" in e for e in errors)

    def test_targeted_info_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(to="core-agent", type="info")))
        assert errors == []


class TestExpectsResponse:
    def test_absent_ok(self, tmp_path):
        errors, warnings = validate_message(_write_msg(tmp_path, _valid_fm()))
        assert errors == []
        assert warnings == []

    def test_true_ok(self, tmp_path):
        errors, warnings = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"expects-response": "true"}))
        )
        assert errors == []

    def test_false_ok_for_non_task_assignment(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(type="question", **{"expects-response": "false"}))
        )
        assert errors == []

    def test_false_on_task_assignment_is_error(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(type="task-assignment", **{"expects-response": "false"}))
        )
        assert any("task-assignment" in e and "expects-response" in e for e in errors)

    def test_broadcast_with_expects_response_true_is_warning(self, tmp_path):
        _, warnings = validate_message(
            _write_msg(tmp_path, _valid_fm(to="all", type="contract-change", **{"expects-response": "true"}))
        )
        assert any("broadcast" in w and "expects-response" in w for w in warnings)

    def test_broadcast_without_expects_response_no_warning(self, tmp_path):
        _, warnings = validate_message(
            _write_msg(tmp_path, _valid_fm(to="all", type="contract-change"))
        )
        assert warnings == []

    def test_invalid_non_boolean_is_error(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"expects-response": "maybe"}))
        )
        assert any("expects-response" in e for e in errors)


class TestResponseEffort:
    def test_valid_values(self, tmp_path):
        for effort in ("XS", "S", "M", "L", "XL"):
            errors, _ = validate_message(
                _write_msg(tmp_path, _valid_fm(**{"response-effort": effort}))
            )
            assert errors == [], f"Expected no errors for response-effort: {effort}"

    def test_absent_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm()))
        assert errors == []

    def test_invalid_value_is_error(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"response-effort": "HUGE"}))
        )
        assert any("response-effort" in e for e in errors)

    def test_lowercase_invalid(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"response-effort": "m"}))
        )
        assert any("response-effort" in e for e in errors)


class TestResponseDeadline:
    def test_valid_utc_z(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"response-deadline": "2026-06-04T18:00:00Z"}))
        )
        assert errors == []

    def test_valid_with_offset(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"response-deadline": "2026-06-04T18:00:00+03:00"}))
        )
        assert errors == []

    def test_valid_with_fractional_seconds(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"response-deadline": "2026-06-04T18:00:00.123456Z"}))
        )
        assert errors == []

    def test_absent_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm()))
        assert errors == []

    def test_no_timezone_is_error(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"response-deadline": "2026-06-04T18:00:00"}))
        )
        assert any("response-deadline" in e for e in errors)

    def test_date_only_is_error(self, tmp_path):
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(**{"response-deadline": "2026-06-04"}))
        )
        assert any("response-deadline" in e for e in errors)


class TestBackwardsCompatibility:
    def test_all_three_fields_valid(self, tmp_path):
        fm = _valid_fm(**{
            "expects-response": "true",
            "response-effort": "M",
            "response-deadline": "2026-06-04T18:00:00Z",
        })
        errors, warnings = validate_message(_write_msg(tmp_path, fm))
        assert errors == []
        assert warnings == []

    def test_none_of_the_fields_valid(self, tmp_path):
        errors, warnings = validate_message(_write_msg(tmp_path, _valid_fm()))
        assert errors == []
        assert warnings == []


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
