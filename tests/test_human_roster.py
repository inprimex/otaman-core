"""Tests for otaman_core.human_roster — schema + loader.

Covers tasks 2.1 + 2.2 of the human-roster change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_core.human_roster import (
    APPROVER_ROLE,
    ConfigError,
    DoctorFinding,
    HumanRosterEntry,
    check_approver_config,
    is_approver,
    load_human_roster,
    parse_human_roster,
    resolve_approver,
    resolve_roster_human,
)

# ---------------------------------------------------------------------------
# parse_human_roster — happy paths


class TestParseHappyPath:
    def test_minimal_entry(self):
        entries = parse_human_roster(
            [
                {"name": "Alice", "email": "a@x.com", "roles": ["cto"]},
            ]
        )
        assert len(entries) == 1
        e = entries[0]
        assert e.name == "Alice"
        assert e.email == "a@x.com"
        assert e.roles == ["cto"]
        assert e.pm_user_id is None

    def test_entry_with_pm_user_id(self):
        entries = parse_human_roster(
            [
                {"name": "Roman", "email": "r@x.com", "roles": ["cofounder"], "pm-user-id": 1},
            ]
        )
        assert entries[0].pm_user_id == 1

    def test_pm_user_id_underscored_alias_accepted(self):
        """Mirrors the pm_sync hyphen/underscore tolerance."""
        entries = parse_human_roster(
            [
                {"name": "Roman", "email": "r@x.com", "roles": ["cto"], "pm_user_id": 7},
            ]
        )
        assert entries[0].pm_user_id == 7

    def test_multiple_roles(self):
        entries = parse_human_roster(
            [
                {"name": "R", "email": "r@x.com", "roles": ["cofounder", "cto", "cpo"]},
            ]
        )
        assert entries[0].roles == ["cofounder", "cto", "cpo"]

    def test_multiple_entries(self):
        entries = parse_human_roster(
            [
                {"name": "R", "email": "r@x.com", "roles": ["cofounder"], "pm-user-id": 1},
                {"name": "A", "email": "a@x.com", "roles": ["developer"]},
            ]
        )
        assert len(entries) == 2
        assert entries[0].pm_user_id == 1
        assert entries[1].pm_user_id is None

    def test_none_returns_empty(self):
        assert parse_human_roster(None) == []

    def test_empty_list_returns_empty(self):
        assert parse_human_roster([]) == []


# ---------------------------------------------------------------------------
# parse_human_roster — error paths


class TestParseErrors:
    def test_empty_roles_raises_with_name(self):
        with pytest.raises(ConfigError) as ei:
            parse_human_roster(
                [
                    {"name": "Alice", "email": "a@x.com", "roles": []},
                ]
            )
        # Error message must reference the entry's name so users can find it.
        assert "Alice" in str(ei.value)
        assert "roles" in str(ei.value)

    def test_missing_name_raises(self):
        with pytest.raises(ConfigError, match="name"):
            parse_human_roster(
                [
                    {"email": "a@x.com", "roles": ["cto"]},
                ]
            )

    def test_missing_email_is_optional(self):
        # hitl-default-approver D3: an approver enrolled from a non-email key
        # comment parses with email=None (otaman doctor WARNs); no ConfigError.
        (entry,) = parse_human_roster([{"name": "key-comment", "roles": [APPROVER_ROLE]}])
        assert entry.email is None
        assert entry.name == "key-comment"

    def test_present_but_empty_email_raises(self):
        with pytest.raises(ConfigError, match="email"):
            parse_human_roster([{"name": "Alice", "email": "", "roles": ["cto"]}])

    def test_missing_roles_raises(self):
        with pytest.raises(ConfigError, match="roles"):
            parse_human_roster(
                [
                    {"name": "Alice", "email": "a@x.com"},
                ]
            )

    def test_roles_not_list_raises(self):
        with pytest.raises(ConfigError, match="roles"):
            parse_human_roster(
                [
                    {"name": "Alice", "email": "a@x.com", "roles": "cto"},
                ]
            )

    def test_role_value_not_string_raises(self):
        with pytest.raises(ConfigError, match="roles"):
            parse_human_roster(
                [
                    {"name": "Alice", "email": "a@x.com", "roles": [42]},
                ]
            )

    def test_block_not_list_raises(self):
        with pytest.raises(ConfigError, match="list"):
            parse_human_roster({"name": "Alice"})

    def test_entry_not_mapping_raises(self):
        with pytest.raises(ConfigError, match="mapping"):
            parse_human_roster(["not-a-dict"])

    def test_pm_user_id_string_raises(self):
        with pytest.raises(ConfigError, match="pm-user-id"):
            parse_human_roster(
                [
                    {"name": "A", "email": "a@x.com", "roles": ["cto"], "pm-user-id": "1"},
                ]
            )

    def test_pm_user_id_bool_raises(self):
        """Bools are technically ints in Python — reject explicitly."""
        with pytest.raises(ConfigError, match="pm-user-id"):
            parse_human_roster(
                [
                    {"name": "A", "email": "a@x.com", "roles": ["cto"], "pm-user-id": True},
                ]
            )

    def test_error_uses_index_when_name_missing(self):
        with pytest.raises(ConfigError, match=r"\[0\]"):
            parse_human_roster(
                [
                    {"email": "a@x.com", "roles": ["cto"]},
                ]
            )


# ---------------------------------------------------------------------------
# load_human_roster — file I/O


class TestLoadHumanRoster:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert load_human_roster(tmp_path / "nope.yaml") == []

    def test_file_without_block_returns_empty(self, tmp_path: Path):
        p = tmp_path / "platform.yaml"
        p.write_text("project: x\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
        assert load_human_roster(p) == []

    def test_file_with_block_loads(self, tmp_path: Path):
        p = tmp_path / "platform.yaml"
        p.write_text(
            "human-roster:\n"
            "  - name: Roman\n"
            "    email: r@x.com\n"
            "    roles: [cofounder, cto]\n"
            "    pm-user-id: 1\n",
            encoding="utf-8",
        )
        roster = load_human_roster(p)
        assert len(roster) == 1
        assert roster[0].name == "Roman"
        assert roster[0].pm_user_id == 1
        assert roster[0].roles == ["cofounder", "cto"]

    def test_file_with_invalid_block_raises(self, tmp_path: Path):
        """Structural errors propagate — silent failure would mean bridges
        create unassigned issues mysteriously."""
        p = tmp_path / "platform.yaml"
        p.write_text(
            "human-roster:\n  - name: Alice\n    email: a@x.com\n    roles: []\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_human_roster(p)

    def test_corrupt_yaml_returns_empty(self, tmp_path: Path):
        p = tmp_path / "platform.yaml"
        p.write_text("not: valid: yaml: at: all: [", encoding="utf-8")
        assert load_human_roster(p) == []


# ---------------------------------------------------------------------------
# HumanRosterEntry dataclass


class TestHumanRosterEntry:
    def test_construction_with_defaults(self):
        e = HumanRosterEntry(name="A", email="a@x.com", roles=["cto"])
        assert e.pm_user_id is None

    def test_construction_with_all_fields(self):
        e = HumanRosterEntry(name="A", email="a@x.com", roles=["cto"], pm_user_id=42)
        assert e.pm_user_id == 42

    def test_frozen(self):
        e = HumanRosterEntry(name="A", email="a@x.com", roles=["cto"])
        with pytest.raises(AttributeError):
            e.name = "B"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# approver role model (hitl-default-approver 1.1)


def _roster(*entries: dict) -> list[HumanRosterEntry]:
    return parse_human_roster(list(entries))


class TestResolveRosterHuman:
    def test_resolves_by_name_case_insensitive(self):
        roster = _roster({"name": "Ana", "email": "a@x.com", "roles": [APPROVER_ROLE]})
        assert resolve_roster_human(roster, "ana").name == "Ana"
        assert resolve_roster_human(roster, "ANA").name == "Ana"

    def test_resolves_by_name_slug(self):
        roster = _roster({"name": "Jane Doe", "email": "j@x.com", "roles": ["developer"]})
        assert resolve_roster_human(roster, "jane-doe").name == "Jane Doe"

    def test_resolves_by_email_and_local_part(self):
        roster = _roster({"name": "X", "email": "guest@x.com", "roles": ["developer"]})
        assert resolve_roster_human(roster, "guest@x.com").name == "X"
        assert resolve_roster_human(roster, "guest").name == "X"

    def test_unresolved_returns_none(self):
        roster = _roster({"name": "Ana", "email": "a@x.com", "roles": [APPROVER_ROLE]})
        assert resolve_roster_human(roster, "nobody") is None
        assert resolve_roster_human(roster, "") is None
        assert resolve_roster_human(roster, None) is None

    def test_email_optional_entry_resolves_by_name(self):
        roster = _roster({"name": "key-comment", "roles": [APPROVER_ROLE]})
        assert resolve_roster_human(roster, "key-comment").email is None


class TestIsApproverAndResolveApprover:
    def test_is_approver(self):
        assert is_approver(HumanRosterEntry("A", "a@x.com", [APPROVER_ROLE, "cto"])) is True
        assert is_approver(HumanRosterEntry("A", "a@x.com", ["developer"])) is False

    def test_arbitrary_roles_still_accepted(self):
        # approver coexists with any additional role strings, unchanged.
        (entry,) = _roster({"name": "A", "email": "a@x.com", "roles": ["cofounder", APPROVER_ROLE]})
        assert entry.roles == ["cofounder", "approver"]
        assert is_approver(entry)

    def test_resolve_approver_returns_entry_only_when_approver(self):
        roster = _roster(
            {"name": "Ana", "email": "a@x.com", "roles": [APPROVER_ROLE]},
            {"name": "Guest", "email": "g@x.com", "roles": ["developer"]},
        )
        assert resolve_approver(roster, "ana").name == "Ana"
        assert resolve_approver(roster, "guest") is None  # resolves but not approver
        assert resolve_approver(roster, "nobody") is None  # unresolved


class TestCheckApproverConfig:
    def test_error_when_hitl_configured_and_no_approver(self):
        roster = _roster({"name": "A", "email": "a@x.com", "roles": ["developer"]})
        findings = check_approver_config(roster, hitl_configured=True)
        assert [f.level for f in findings] == ["error"]
        assert APPROVER_ROLE in findings[0].message

    def test_error_when_pending_proposals_and_no_approver(self):
        findings = check_approver_config([], pending_proposals=True)
        assert findings and findings[0].level == "error"

    def test_error_when_roster_empty_list(self):
        # (b) an explicitly empty roster is "no approver" — ERROR fires.
        findings = check_approver_config([], hitl_configured=True)
        assert [f.level for f in findings] == ["error"]

    def test_error_when_roster_block_absent(self, tmp_path: Path):
        # (c) opt-out tenant: platform.yaml with NO human-roster block.
        # load_human_roster -> [] -> the live path still has no approver -> ERROR.
        # (Regression guard for deploy 3.2 E2E: doctor must flag absent rosters.)
        p = tmp_path / "platform.yaml"
        p.write_text("project: x\nversion: '1.0'\nrepos: []\n", encoding="utf-8")
        roster = load_human_roster(p)
        assert roster == []
        findings = check_approver_config(roster, hitl_configured=True, pending_proposals=True)
        assert [f.level for f in findings] == ["error"]
        assert APPROVER_ROLE in findings[0].message

    def test_no_error_when_approver_present(self):
        roster = _roster({"name": "A", "email": "a@x.com", "roles": [APPROVER_ROLE]})
        findings = check_approver_config(roster, hitl_configured=True, pending_proposals=True)
        assert [f for f in findings if f.level == "error"] == []

    def test_no_error_when_path_not_live(self):
        # No HITL adapters and no pending proposals -> nothing to action -> no error.
        assert check_approver_config([], hitl_configured=False, pending_proposals=False) == []

    def test_warn_on_approver_missing_email(self):
        roster = _roster({"name": "key-comment", "roles": [APPROVER_ROLE]})
        findings = check_approver_config(roster, hitl_configured=True)
        # approver exists (no error) but has no email (warn)
        assert [f.level for f in findings] == ["warn"]
        assert "key-comment" in findings[0].message

    def test_finding_is_frozen_dataclass(self):
        f = DoctorFinding("warn", "m")
        with pytest.raises(AttributeError):
            f.level = "error"  # type: ignore[misc]
