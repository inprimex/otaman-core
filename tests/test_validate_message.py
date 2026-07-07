"""Tests for otaman_core.validate_message and .otaman marker agent field."""

from __future__ import annotations
import os
import sys
from pathlib import Path
import pytest
from otaman_core.validate_message import PRIVILEGED_TYPES, validate_message, validate_message_content


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
        """emergency-halt is privileged (F012) — from: human required to isolate the broadcast check."""
        errors, _ = validate_message(
            _write_msg(tmp_path, _valid_fm(to="all", type="emergency-halt", **{"from": "human"}))
        )
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


class TestPathField:
    """monorepo-path-ownership task 1.6 — `path:` + `repo:` validation."""

    def test_absent_ok(self, tmp_path):
        errors, _ = validate_message(_write_msg(tmp_path, _valid_fm()))
        assert errors == []

    def test_string_path_on_eligible_type_ok(self, tmp_path):
        fm = _valid_fm(
            type="task-assignment",
            repo="mono",
            path="apps/web/page.tsx",
        )
        errors, _ = validate_message(_write_msg(tmp_path, fm))
        assert errors == []

    def test_list_path_on_eligible_type_ok(self, tmp_path):
        fm = _valid_fm(
            type="contract-change",
            to="all",
            repo="mono",
            path="[apps/web/x, apps/api/y]",
        )
        errors, _ = validate_message(_write_msg(tmp_path, fm))
        assert errors == []

    def test_path_on_info_type_rejected(self, tmp_path):
        fm = _valid_fm(type="info", repo="mono", path="x.py")
        errors, _ = validate_message(_write_msg(tmp_path, fm))
        assert any("path" in e and "info" in e for e in errors)

    def test_path_without_repo_rejected(self, tmp_path):
        fm = _valid_fm(type="task-assignment", path="x.py")
        errors, _ = validate_message(_write_msg(tmp_path, fm))
        assert any("repo" in e for e in errors)

    def test_empty_string_path_rejected(self, tmp_path):
        fm = _valid_fm(type="task-assignment", repo="mono", path='""')
        errors, _ = validate_message(_write_msg(tmp_path, fm))
        assert any("path" in e for e in errors)

    def test_empty_list_path_rejected(self, tmp_path):
        fm = _valid_fm(type="task-assignment", repo="mono", path="[]")
        errors, _ = validate_message(_write_msg(tmp_path, fm))
        assert any("path" in e and "list" in e for e in errors)

    def test_non_string_in_list_rejected(self, tmp_path):
        fm = _valid_fm(type="task-assignment", repo="mono", path="[apps/x, 42]")
        errors, _ = validate_message(_write_msg(tmp_path, fm))
        assert any("path" in e for e in errors)

    def test_path_allowed_types(self, tmp_path):
        for t in ("task-assignment", "task-complete", "spec-change-request", "contract-change"):
            if t == "contract-change":
                fm = _valid_fm(type=t, to="all", repo="mono", path="x.py")
            else:
                fm = _valid_fm(type=t, repo="mono", path="x.py")
            errors, _ = validate_message(_write_msg(tmp_path, fm))
            assert errors == [], f"{t}: {errors}"


class TestNewMessageTypes:
    """Allowlist sync for types added by shipped spec changes (2026-06-07)."""

    NEW_TYPES = [
        "request-human-review",
        "human-decision",
        "outcome-estimate-requested",
        "outcome-estimates-ready",
        "outcome-cost-accepted",
        "outcome-cost-rejected",
        "outcome-status-changed",
        "solution-status-changed",
        "solution-recommendation",
        "outcome-proposal",
    ]

    def test_each_type_accepted(self, tmp_path):
        for t in self.NEW_TYPES:
            # human-decision is privileged (F012) — needs from: human, unlike the rest.
            from_field = "human" if t in PRIVILEGED_TYPES else "core-agent"
            errors, _ = validate_message(_write_msg(tmp_path, _valid_fm(type=t, **{"from": from_field})))
            assert errors == [], f"Expected {t!r} to validate, got errors: {errors}"


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


class TestPrivilegedTypes:
    """F012: privileged types (assert a human decision) require from: human."""

    def test_all_privileged_types_covered(self):
        assert PRIVILEGED_TYPES == {
            "human-decision",
            "spec-change-approved",
            "spec-change-rejected",
            "emergency-halt",
        }

    @pytest.mark.parametrize("ptype", sorted(PRIVILEGED_TYPES))
    def test_privileged_type_from_agent_rejected(self, tmp_path, ptype):
        errors = _errors(tmp_path, _valid_fm(type=ptype, **{"from": "core-agent"}))
        assert any("privileged" in e for e in errors), errors

    @pytest.mark.parametrize("ptype", sorted(PRIVILEGED_TYPES))
    def test_privileged_type_from_human_accepted(self, tmp_path, ptype):
        errors = _errors(tmp_path, _valid_fm(type=ptype, **{"from": "human"}))
        assert not any("privileged" in e for e in errors), errors

    def test_privileged_type_from_spoofed_human_lookalike_rejected(self, tmp_path):
        """from: 'Human' / 'human-agent' etc. must not satisfy the check."""
        errors = _errors(tmp_path, _valid_fm(type="spec-change-approved", **{"from": "Human"}))
        assert any("privileged" in e for e in errors), errors

    def test_non_privileged_type_unaffected(self, tmp_path):
        errors = _errors(tmp_path, _valid_fm(type="info", **{"from": "core-agent"}))
        assert errors == []


class TestValidateMessageContent:
    """validate_message_content is the pre-write core validate_message() wraps."""

    def test_matches_file_based_validation(self, tmp_path):
        fm = _valid_fm(type="spec-change-approved", **{"from": "core-agent"})
        content = f"---\n{fm}\n---\n\n## Subject: test\n"
        content_errors, content_warnings = validate_message_content(content)
        file_errors, file_warnings = validate_message(_write_msg(tmp_path, fm))
        assert content_errors == file_errors
        assert content_warnings == file_warnings

    def test_valid_content_no_file_needed(self):
        fm = _valid_fm()
        content = f"---\n{fm}\n---\n\n## Subject: test\n"
        errors, warnings = validate_message_content(content)
        assert errors == []
        assert warnings == []


class TestStdinMode:
    """main() --stdin: the hook-facing entry point for pre-write validation."""

    def _run_stdin(self, content: str, cwd: Path) -> tuple[int, str]:
        import io
        import contextlib
        from otaman_core.validate_message import main

        old_argv = sys.argv
        old_stdin = sys.stdin
        old_cwd = Path.cwd()
        sys.argv = ["validate-message.py", "--stdin"]
        sys.stdin = io.StringIO(content)
        stderr = io.StringIO()
        os.chdir(cwd)
        try:
            with contextlib.redirect_stderr(stderr):
                code = main()
        finally:
            sys.argv = old_argv
            sys.stdin = old_stdin
            os.chdir(old_cwd)
        return code, stderr.getvalue()

    def test_forged_privileged_message_rejected(self, tmp_path):
        fm = _valid_fm(type="spec-change-approved", **{"from": "core-agent"})
        content = f"---\n{fm}\n---\n\n## Subject: test\n"
        code, stderr = self._run_stdin(content, tmp_path)
        assert code == 1
        assert "privileged" in stderr

    def test_legitimate_privileged_message_accepted(self, tmp_path):
        fm = _valid_fm(type="spec-change-approved", **{"from": "human"})
        content = f"---\n{fm}\n---\n\n## Subject: test\n"
        code, _ = self._run_stdin(content, tmp_path)
        assert code == 0

    def test_ordinary_message_accepted(self, tmp_path):
        content = f"---\n{_valid_fm()}\n---\n\n## Subject: test\n"
        code, _ = self._run_stdin(content, tmp_path)
        assert code == 0
