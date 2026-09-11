"""Tests for otaman_core.accounts — per-human config_dir resolver (team-mode 2.3a)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    pytest.skip("PyYAML not installed", allow_module_level=True)

from otaman_core.accounts import launch_settings_path, resolve_human_config_dir


def _write_ls(org_root: Path, data: dict) -> None:
    org_root.mkdir(parents=True, exist_ok=True)
    (org_root / "launch-settings.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_launch_settings_path():
    assert launch_settings_path(Path("/o/acme")) == Path("/o/acme/launch-settings.yaml")


def test_resolves_matching_human(tmp_path):
    org = tmp_path / "orgs" / "acme"
    _write_ls(
        org,
        {
            "accounts": {
                "profile-a": {"human": "roman@x.com", "config_dir": "/home/roman/.claude"},
                "profile-b": {"human": "dev@x.com", "config_dir": "/home/dev/.claude"},
            }
        },
    )
    assert resolve_human_config_dir(org, "roman@x.com") == Path("/home/roman/.claude")
    assert resolve_human_config_dir(org, "dev@x.com") == Path("/home/dev/.claude")


def test_match_is_case_insensitive(tmp_path):
    org = tmp_path / "orgs" / "acme"
    _write_ls(org, {"accounts": {"a": {"human": "Roman@X.com", "config_dir": "/c/r"}}})
    assert resolve_human_config_dir(org, "roman@x.com") == Path("/c/r")


def test_expands_tilde_and_env(tmp_path, monkeypatch):
    # OS-agnostic: assert expansion matches the stdlib expanduser/expandvars the
    # resolver uses, rather than a hardcoded POSIX path (Windows ~ uses USERPROFILE).
    org = tmp_path / "orgs" / "acme"
    monkeypatch.setenv("OT_CD", str(tmp_path / "cd"))
    _write_ls(org, {"accounts": {"a": {"human": "r", "config_dir": "~/.claude-r"}}})
    assert resolve_human_config_dir(org, "r") == Path(os.path.expanduser("~/.claude-r"))
    _write_ls(org, {"accounts": {"a": {"human": "r", "config_dir": "$OT_CD"}}})
    assert resolve_human_config_dir(org, "r") == Path(str(tmp_path / "cd"))


def test_unmapped_human_is_none(tmp_path):
    org = tmp_path / "orgs" / "acme"
    _write_ls(org, {"accounts": {"a": {"human": "roman@x.com", "config_dir": "/c/r"}}})
    assert resolve_human_config_dir(org, "someone-else@x.com") is None


def test_no_accounts_block_is_none(tmp_path):
    # the live case today: launch-settings has only models:
    org = tmp_path / "orgs" / "acme"
    _write_ls(org, {"models": {"default": "sonnet"}})
    assert resolve_human_config_dir(org, "roman@x.com") is None


def test_no_launch_settings_file_is_none(tmp_path):
    org = tmp_path / "orgs" / "acme"
    org.mkdir(parents=True)
    assert resolve_human_config_dir(org, "roman@x.com") is None


def test_matched_human_without_config_dir_is_none(tmp_path):
    org = tmp_path / "orgs" / "acme"
    _write_ls(org, {"accounts": {"a": {"human": "roman@x.com"}}})  # no config_dir
    assert resolve_human_config_dir(org, "roman@x.com") is None


def test_blank_human_sub_is_none(tmp_path):
    org = tmp_path / "orgs" / "acme"
    _write_ls(org, {"accounts": {"a": {"human": "roman@x.com", "config_dir": "/c/r"}}})
    assert resolve_human_config_dir(org, "   ") is None


def test_malformed_launch_settings_is_none(tmp_path):
    org = tmp_path / "orgs" / "acme"
    org.mkdir(parents=True)
    (org / "launch-settings.yaml").write_text("{ not: valid: yaml", encoding="utf-8")
    assert resolve_human_config_dir(org, "roman@x.com") is None


def test_never_returns_credentials_only_a_path(tmp_path):
    # values-free: even if an account carries other fields, only config_dir surfaces
    org = tmp_path / "orgs" / "acme"
    _write_ls(
        org,
        {"accounts": {"a": {"human": "r", "config_dir": "/c/r", "oauth_token": "SECRET"}}},
    )
    result = resolve_human_config_dir(org, "r")
    assert result == Path("/c/r")
    assert "SECRET" not in str(result)
