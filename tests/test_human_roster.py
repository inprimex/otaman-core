"""Tests for otaman_core.human_roster — schema + loader.

Covers tasks 2.1 + 2.2 of the human-roster change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_core.human_roster import (
    ConfigError,
    HumanRosterEntry,
    load_human_roster,
    parse_human_roster,
)

# ---------------------------------------------------------------------------
# parse_human_roster — happy paths


class TestParseHappyPath:
    def test_minimal_entry(self):
        entries = parse_human_roster([
            {"name": "Alice", "email": "a@x.com", "roles": ["cto"]},
        ])
        assert len(entries) == 1
        e = entries[0]
        assert e.name == "Alice"
        assert e.email == "a@x.com"
        assert e.roles == ["cto"]
        assert e.pm_user_id is None

    def test_entry_with_pm_user_id(self):
        entries = parse_human_roster([
            {"name": "Roman", "email": "r@x.com", "roles": ["cofounder"], "pm-user-id": 1},
        ])
        assert entries[0].pm_user_id == 1

    def test_pm_user_id_underscored_alias_accepted(self):
        """Mirrors the pm_sync hyphen/underscore tolerance."""
        entries = parse_human_roster([
            {"name": "Roman", "email": "r@x.com", "roles": ["cto"], "pm_user_id": 7},
        ])
        assert entries[0].pm_user_id == 7

    def test_multiple_roles(self):
        entries = parse_human_roster([
            {"name": "R", "email": "r@x.com", "roles": ["cofounder", "cto", "cpo"]},
        ])
        assert entries[0].roles == ["cofounder", "cto", "cpo"]

    def test_multiple_entries(self):
        entries = parse_human_roster([
            {"name": "R", "email": "r@x.com", "roles": ["cofounder"], "pm-user-id": 1},
            {"name": "A", "email": "a@x.com", "roles": ["developer"]},
        ])
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
            parse_human_roster([
                {"name": "Alice", "email": "a@x.com", "roles": []},
            ])
        # Error message must reference the entry's name so users can find it.
        assert "Alice" in str(ei.value)
        assert "roles" in str(ei.value)

    def test_missing_name_raises(self):
        with pytest.raises(ConfigError, match="name"):
            parse_human_roster([
                {"email": "a@x.com", "roles": ["cto"]},
            ])

    def test_missing_email_raises(self):
        with pytest.raises(ConfigError, match="email"):
            parse_human_roster([
                {"name": "Alice", "roles": ["cto"]},
            ])

    def test_missing_roles_raises(self):
        with pytest.raises(ConfigError, match="roles"):
            parse_human_roster([
                {"name": "Alice", "email": "a@x.com"},
            ])

    def test_roles_not_list_raises(self):
        with pytest.raises(ConfigError, match="roles"):
            parse_human_roster([
                {"name": "Alice", "email": "a@x.com", "roles": "cto"},
            ])

    def test_role_value_not_string_raises(self):
        with pytest.raises(ConfigError, match="roles"):
            parse_human_roster([
                {"name": "Alice", "email": "a@x.com", "roles": [42]},
            ])

    def test_block_not_list_raises(self):
        with pytest.raises(ConfigError, match="list"):
            parse_human_roster({"name": "Alice"})

    def test_entry_not_mapping_raises(self):
        with pytest.raises(ConfigError, match="mapping"):
            parse_human_roster(["not-a-dict"])

    def test_pm_user_id_string_raises(self):
        with pytest.raises(ConfigError, match="pm-user-id"):
            parse_human_roster([
                {"name": "A", "email": "a@x.com", "roles": ["cto"], "pm-user-id": "1"},
            ])

    def test_pm_user_id_bool_raises(self):
        """Bools are technically ints in Python — reject explicitly."""
        with pytest.raises(ConfigError, match="pm-user-id"):
            parse_human_roster([
                {"name": "A", "email": "a@x.com", "roles": ["cto"], "pm-user-id": True},
            ])

    def test_error_uses_index_when_name_missing(self):
        with pytest.raises(ConfigError, match=r"\[0\]"):
            parse_human_roster([
                {"email": "a@x.com", "roles": ["cto"]},
            ])


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
            "human-roster:\n"
            "  - name: Alice\n"
            "    email: a@x.com\n"
            "    roles: []\n",
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
        with pytest.raises(Exception):
            e.name = "B"  # type: ignore[misc]
