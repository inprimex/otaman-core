"""Tests for otaman_core.identity — F013 enforcement-identity resolver.

Covers the security property this module exists for: OTAMAN_AGENT and
.agents/current-agent must NOT influence enforcement-grade resolution, even
though they remain valid for the separate, general-purpose CLI display
chain (otaman-cli's own identity.py, untouched by this module).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otaman_core.identity import resolve_agent_identity, resolve_enforcement_identity

AUDIT_LOG = Path(".agents") / "audit" / "identity-resolutions.jsonl"


@pytest.fixture
def workspace(tmp_path):
    """otaman root + a managed repo, mirroring test_resolve.py's fixture."""
    root = tmp_path / "my-otaman-meta"
    root.mkdir()
    (root / "platform.yaml").write_text("project: test\n")
    (root / ".agents").mkdir()

    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    return {"root": root, "repo": repo}


def _read_audit_entries(root: Path) -> list[dict]:
    path = root / AUDIT_LOG
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


class TestMarkerResolution:
    def test_resolves_from_file_shape_marker(self, workspace):
        repo = workspace["repo"]
        (repo / ".otaman").write_text("otaman_root: ../my-otaman-meta\nagent: core-agent\n")
        result = resolve_enforcement_identity(repo)
        assert result.agent == "core-agent"
        assert result.source == "dotoman-marker"

    def test_resolves_from_directory_shape_marker(self, workspace):
        repo = workspace["repo"]
        marker_dir = repo / ".otaman"
        marker_dir.mkdir()
        (marker_dir / "agent").write_text("human\n")
        result = resolve_enforcement_identity(repo)
        assert result.agent == "human"

    def test_walks_up_past_marker_without_agent_field(self, workspace):
        repo = workspace["repo"]
        (repo / ".otaman").write_text("otaman_root: ../my-otaman-meta\n")
        subdir = repo / "src" / "nested"
        subdir.mkdir(parents=True)
        # Put an agent-bearing marker higher up (the workspace root itself)
        (workspace["root"].parent / ".otaman").write_text("agent: root-agent\n")
        result = resolve_enforcement_identity(subdir)
        assert result.agent == "root-agent"

    def test_no_marker_anywhere_is_unresolved(self, tmp_path):
        lonely = tmp_path / "no-otaman-here"
        lonely.mkdir()
        result = resolve_enforcement_identity(lonely)
        assert result.agent is None
        assert result.source == "unresolved"


class TestSpoofableSignalsAreIgnored:
    """The whole point of F013: these must NOT affect enforcement resolution."""

    def test_otaman_agent_env_var_is_ignored(self, workspace, monkeypatch):
        monkeypatch.setenv("OTAMAN_AGENT", "attacker-agent")
        repo = workspace["repo"]
        # No .otaman marker at all — env var must not fill the gap.
        result = resolve_enforcement_identity(repo)
        assert result.agent is None
        assert result.agent != "attacker-agent"

    def test_env_var_does_not_override_marker(self, workspace, monkeypatch):
        monkeypatch.setenv("OTAMAN_AGENT", "attacker-agent")
        repo = workspace["repo"]
        (repo / ".otaman").write_text("agent: core-agent\n")
        result = resolve_enforcement_identity(repo)
        assert result.agent == "core-agent"

    def test_current_agent_file_is_ignored(self, workspace):
        (workspace["root"] / ".agents" / "current-agent").write_text("attacker-agent\n")
        repo = workspace["repo"]
        # No .otaman marker — legacy current-agent must not be consulted.
        result = resolve_enforcement_identity(repo)
        assert result.agent is None
        assert result.agent != "attacker-agent"


class TestAuditLog:
    def test_resolved_identity_is_logged(self, workspace):
        repo = workspace["repo"]
        (repo / ".otaman").write_text("otaman_root: ../my-otaman-meta\nagent: core-agent\n")
        resolve_enforcement_identity(repo)
        entries = _read_audit_entries(workspace["root"])
        assert len(entries) == 1
        assert entries[0]["agent"] == "core-agent"
        assert entries[0]["source"] == "dotoman-marker"
        assert "timestamp" in entries[0]

    def test_unresolved_identity_is_also_logged(self, workspace):
        repo = workspace["repo"]
        # otaman_root link only (no agent: field) — root is findable for the
        # audit log, but identity itself is genuinely unresolved.
        (repo / ".otaman").write_text("otaman_root: ../my-otaman-meta\n")
        resolve_enforcement_identity(repo)
        entries = _read_audit_entries(workspace["root"])
        assert len(entries) == 1
        assert entries[0]["agent"] is None
        assert entries[0]["source"] == "unresolved"

    def test_multiple_resolutions_append_not_overwrite(self, workspace):
        repo = workspace["repo"]
        (repo / ".otaman").write_text("otaman_root: ../my-otaman-meta\nagent: core-agent\n")
        resolve_enforcement_identity(repo)
        resolve_enforcement_identity(repo)
        resolve_enforcement_identity(repo)
        entries = _read_audit_entries(workspace["root"])
        assert len(entries) == 3

    def test_no_otaman_root_does_not_raise(self, tmp_path):
        lonely = tmp_path / "totally-unmanaged"
        lonely.mkdir()
        # Must not raise even though there's nowhere to write an audit log.
        result = resolve_enforcement_identity(lonely)
        assert result.agent is None


class TestResolveAgentIdentity:
    """team-mode B1: the shared DISPLAY/session resolver (cwd-owner authoritative)."""

    def _project(self, tmp_path):
        """A project_root (platform.yaml dir) + two repo dirs on disk."""
        root = tmp_path / "meta"
        root.mkdir()
        (root / "platform.yaml").write_text(
            "project: t\n"
            "version: '1.0'\n"
            "repos:\n"
            "  - name: core\n"
            "    owner: core-agent\n"
            "    path: ../otaman-core\n"
            "  - name: mono\n"
            "    owner: root-agent\n"
            "    path: ../mono\n"
            "    owner-paths:\n"
            "      apps/web/**: web-agent\n",
            encoding="utf-8",
        )
        (tmp_path / "otaman-core").mkdir()
        (tmp_path / "mono" / "apps" / "web" / "ui").mkdir(parents=True)
        return root

    def test_cwd_owner_resolves(self, tmp_path):
        root = self._project(tmp_path)
        got = resolve_agent_identity(tmp_path / "otaman-core", {}, project_root=root)
        assert got == "core-agent"

    def test_cwd_owner_overrides_poisoned_env(self, tmp_path):
        # THE security property: a poisoned OTAMAN_AGENT cannot override the cwd owner
        root = self._project(tmp_path)
        got = resolve_agent_identity(
            tmp_path / "otaman-core", {"OTAMAN_AGENT": "evil-agent"}, project_root=root
        )
        assert got == "core-agent"

    def test_owner_paths_glob_wins(self, tmp_path):
        # a subdir UNDER apps/web/ matches the apps/web/** glob → web-agent
        root = self._project(tmp_path)
        got = resolve_agent_identity(
            tmp_path / "mono" / "apps" / "web" / "ui", {}, project_root=root
        )
        assert got == "web-agent"

    def test_env_used_only_when_cwd_unowned(self, tmp_path):
        root = self._project(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        got = resolve_agent_identity(outside, {"OTAMAN_AGENT": "runner-agent"}, project_root=root)
        assert got == "runner-agent"

    def test_unresolved_is_none(self, tmp_path):
        root = self._project(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        assert resolve_agent_identity(outside, {}, project_root=root) is None

    def test_no_platform_falls_back_to_env(self, tmp_path):
        # no platform.yaml under project_root → cwd unresolved → env
        empty = tmp_path / "empty"
        empty.mkdir()
        got = resolve_agent_identity(empty, {"OTAMAN_AGENT": "x-agent"}, project_root=empty)
        assert got == "x-agent"

    def test_blank_env_is_none(self, tmp_path):
        root = self._project(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        assert resolve_agent_identity(outside, {"OTAMAN_AGENT": "   "}, project_root=root) is None
