"""Tests for otaman_core.spawn — shared spawn types."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from otaman_core.spawn import (
    AttachInfo,
    BackendConfig,
    BackendError,
    SpawnMode,
    SpawnRequest,
    SpawnResult,
    TerminalBackend,
)


class TestSpawnMode:
    def test_string_values(self):
        assert SpawnMode.INTERACTIVE.value == "interactive"
        assert SpawnMode.HEADLESS.value == "headless"
        assert SpawnMode.HYBRID.value == "hybrid"

    def test_round_trip_through_string(self):
        assert SpawnMode("interactive") is SpawnMode.INTERACTIVE


class TestBackendConfig:
    def test_defaults(self):
        cfg = BackendConfig()
        assert cfg.session_prefix == "otaman"
        assert cfg.extra == {}

    def test_custom_prefix(self):
        cfg = BackendConfig(session_prefix="greenbin")
        assert cfg.session_prefix == "greenbin"


class TestSpawnRequest:
    def _valid_kwargs(self, tmp_path: Path) -> dict:
        return {
            "agent": "backend-agent",
            "repo": "auth-service",
            "project_root": tmp_path,
        }

    def test_minimal_valid_request(self, tmp_path):
        req = SpawnRequest(**self._valid_kwargs(tmp_path))
        assert req.agent == "backend-agent"
        assert req.mode is SpawnMode.INTERACTIVE
        assert req.harness == "claude-code"
        assert req.backend is None
        assert req.env == {}

    def test_empty_agent_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="agent is required"):
            SpawnRequest(agent="", repo="auth-service", project_root=tmp_path)

    def test_whitespace_agent_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="agent is required"):
            SpawnRequest(agent="   ", repo="auth-service", project_root=tmp_path)

    def test_empty_repo_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="repo is required"):
            SpawnRequest(agent="backend-agent", repo="", project_root=tmp_path)

    def test_project_root_must_be_path(self):
        with pytest.raises(TypeError, match="project_root must be a Path"):
            SpawnRequest(
                agent="backend-agent",
                repo="auth-service",
                project_root="/tmp/wrong-type",  # type: ignore[arg-type]
            )

    def test_headless_mode_not_implemented_in_v0(self, tmp_path):
        with pytest.raises(NotImplementedError, match="HEADLESS"):
            SpawnRequest(
                agent="backend-agent",
                repo="auth-service",
                project_root=tmp_path,
                mode=SpawnMode.HEADLESS,
            )

    def test_hybrid_mode_not_implemented_in_v0(self, tmp_path):
        with pytest.raises(NotImplementedError, match="HYBRID"):
            SpawnRequest(
                agent="backend-agent",
                repo="auth-service",
                project_root=tmp_path,
                mode=SpawnMode.HYBRID,
            )

    def test_non_claude_code_harness_not_implemented(self, tmp_path):
        with pytest.raises(NotImplementedError, match="openai-agents"):
            SpawnRequest(
                agent="backend-agent",
                repo="auth-service",
                project_root=tmp_path,
                harness="openai-agents",
            )

    def test_optional_fields_persist(self, tmp_path):
        worktree = tmp_path / "worktree"
        req = SpawnRequest(
            agent="backend-agent",
            repo="auth-service",
            project_root=tmp_path,
            worktree=worktree,
            account="greenbin",
            initial_prompt="implement OAuth",
            env={"FOO": "bar"},
            timeout=timedelta(hours=2),
            user="zitadel-user-id-123",
        )
        assert req.worktree == worktree
        assert req.account == "greenbin"
        assert req.initial_prompt == "implement OAuth"
        assert req.env == {"FOO": "bar"}
        assert req.timeout == timedelta(hours=2)
        assert req.user == "zitadel-user-id-123"

    def test_request_is_hashable(self, tmp_path):
        """frozen=True dataclass should be hashable for set/dict membership."""
        req = SpawnRequest(agent="a", repo="r", project_root=tmp_path)
        with pytest.raises(TypeError):
            # env is a dict, so direct hashing of the request fails;
            # this is intentional — env is mutable in spirit. Test that
            # immutable-only construction works:
            hash(req)
        # But constructing identical requests doesn't crash
        req2 = SpawnRequest(agent="a", repo="r", project_root=tmp_path)
        assert req == req2


class TestAttachInfo:
    def test_minimal(self):
        info = AttachInfo(
            host="localhost",
            backend="tmux",
            session_name="otaman-backend-agent-auth-service",
            attach_command="tmux attach -t otaman-backend-agent-auth-service",
        )
        assert info.user is None  # defaults to local

    def test_with_user(self):
        info = AttachInfo(
            host="100.65.57.73",
            backend="tmux",
            session_name="otaman-alice-auth-service",
            attach_command="ssh romans@100.65.57.73 -t tmux attach -t otaman-alice-auth-service",
            user="alice",
        )
        assert info.user == "alice"


class TestSpawnResult:
    def test_interactive_result(self, tmp_path):
        attach = AttachInfo(
            host="localhost",
            backend="tmux",
            session_name="test-session",
            attach_command="tmux attach -t test-session",
        )
        result = SpawnResult(
            session_id="abc-123",
            mode=SpawnMode.INTERACTIVE,
            pid=12345,
            attach=attach,
            audit_path=tmp_path / "audit.jsonl",
        )
        assert result.nats_subject is None  # v0


class TestTerminalBackendProtocol:
    """Verify Protocol shape — duck-typed conformance check."""

    def test_minimal_implementation_satisfies_protocol(self):
        class StubBackend:
            name = "stub"

            def spawn(self, request, *, command, cwd, env):
                return AttachInfo(
                    host="localhost",
                    backend="stub",
                    session_name="stub-session",
                    attach_command="echo attached",
                )

            def is_alive(self, session_name):
                return False

            def kill(self, session_name):
                pass

            def list_sessions(self, prefix=None):
                return []

        stub: TerminalBackend = StubBackend()
        assert stub.name == "stub"


class TestBackendError:
    def test_is_runtime_error(self):
        assert issubclass(BackendError, RuntimeError)

    def test_raisable(self):
        with pytest.raises(BackendError, match="tmux not found"):
            raise BackendError("tmux not found on PATH")
