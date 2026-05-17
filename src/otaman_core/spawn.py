"""Spawn types — shared by otaman-runner and the CLI fallback path.

Defines the data model used to request a session spawn, describe the
result, and back the ``TerminalBackend`` Protocol that runner
implementations target.

Per ADR-009: ``otaman-runner`` is the unified spawner. These types live
in ``otaman-core`` so the Mode 1 (solo) CLI fallback can use the same
spawn library function without depending on the runner package.

Out of scope for v0 (deferred per Team-Mode Pilot decision, 2026-05-14):
    - ``HeadlessBackend`` / ``HybridBackend`` (pilot is all interactive)
    - ``WindowsTerminalBackend`` / ``ScreenBackend`` (pilot server is Linux)
    - NATS event publication (Mode 2 stays file-bus + audit log for v0)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Protocol


class SpawnMode(str, Enum):
    """How the spawned session is wrapped for the user.

    INTERACTIVE: human attends in a tmux/Windows-Terminal session;
        AttachInfo is returned so the caller can exec the attach command.
    HEADLESS: no user attends; harness writes transcript to disk/NATS;
        the runner publishes lifecycle events.
    HYBRID: interactive AND transcript-publishing — spectator-friendly.

    Only INTERACTIVE is implemented in v0. HEADLESS / HYBRID stubs
    raise ``NotImplementedError`` so the wiring is forced through one
    code path until they ship.
    """

    INTERACTIVE = "interactive"
    HEADLESS = "headless"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class BackendConfig:
    """Per-backend hint passed through to the chosen ``TerminalBackend``.

    For TmuxBackend the only meaningful field today is ``session_prefix``
    (used to namespace tmux session names per-user when multiple users
    share one runner host). Future backends may use additional fields.
    """

    session_prefix: str = "otaman"
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SpawnRequest:
    """Request a session spawn.

    Validated by the runner before invoking a ``TerminalBackend``. Most
    fields mirror the launch-agents.sh / launch-agents.ps1 inputs so the
    existing launcher UX is preserved.
    """

    agent: str                            # ownership identity, e.g. "backend-agent"
    repo: str                             # owned repo name (from platform.yaml) or absolute path
    project_root: Path                    # absolute path to the maestro folder
    mode: SpawnMode = SpawnMode.INTERACTIVE
    harness: str = "claude-code"          # adapter identifier (ADR-003); v0 only "claude-code"
    backend: BackendConfig | None = None  # None = runner picks the platform default
    worktree: Path | None = None          # absolute path to checkout; None = use repo directly
    account: str | None = None            # CLAUDE_CONFIG_DIR profile name
    initial_prompt: str | None = None     # optional first message to seed the session
    env: dict[str, str] = field(default_factory=dict)
    timeout: timedelta | None = None      # max session lifetime; None = unbounded
    user: str | None = None               # authenticated user id (Zitadel sub claim); None = local

    def __post_init__(self) -> None:
        if not self.agent or not self.agent.strip():
            raise ValueError("SpawnRequest.agent is required")
        if not self.repo or not self.repo.strip():
            raise ValueError("SpawnRequest.repo is required")
        if not isinstance(self.project_root, Path):
            raise TypeError("SpawnRequest.project_root must be a Path")
        if self.mode in (SpawnMode.HEADLESS, SpawnMode.HYBRID):
            raise NotImplementedError(
                f"SpawnMode.{self.mode.name} is not implemented in v0 — "
                "see ADR-009 backlog"
            )
        if self.harness != "claude-code":
            raise NotImplementedError(
                f"harness {self.harness!r} is not implemented in v0 — "
                "only 'claude-code' ships per ADR-003"
            )


@dataclass(frozen=True)
class AttachInfo:
    """How the caller attaches to an interactive session.

    Returned in SpawnResult.attach when mode is INTERACTIVE or HYBRID.
    For HEADLESS sessions this is None.
    """

    host: str                       # hostname / IP to attach to
    backend: str                    # "tmux" | "windows-terminal" | "screen"
    session_name: str               # tmux session name or WT title
    attach_command: str             # exact shell command to attach
    user: str | None = None         # OS user the session runs as


@dataclass(frozen=True)
class SpawnResult:
    """Outcome of a spawn() call."""

    session_id: str                 # uuid; correlation key in audit + future NATS events
    mode: SpawnMode
    pid: int | None                 # OS pid of the harness process, if known
    attach: AttachInfo | None       # interactive / hybrid only
    audit_path: Path                # JSONL audit log location for this session
    nats_subject: str | None = None  # set in Mode 2+ once NATS lands; None in v0


class TerminalBackend(Protocol):
    """Wraps a shell session in a recovery-friendly terminal multiplexer.

    Implementations: TmuxBackend (v0), WindowsTerminalBackend, ScreenBackend,
    HeadlessBackend (post-v0). Each implementation decides how to spawn the
    underlying process, name the session, and produce attach instructions.

    All methods are sync because the implementations shell out to short-lived
    OS commands; the runner daemon wraps them in a thread pool when serving
    HTTP requests.
    """

    name: str   # backend identifier echoed in AttachInfo.backend

    def spawn(
        self,
        request: SpawnRequest,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
    ) -> AttachInfo:
        """Start ``command`` in a wrapped session and return attach info.

        The runner has already resolved the worktree, computed env vars
        (CLAUDE_CONFIG_DIR, OTAMAN_ROOT, OTAMAN_ACTIVE_ROUTING, etc.),
        and decided cwd. The backend's only job is to wrap the command
        in its terminal multiplexer and return how to attach.

        Raises:
            BackendError: on multiplexer failure (e.g. tmux missing,
                session-name collision).
        """
        ...

    def is_alive(self, session_name: str) -> bool:
        """Return True if a session with ``session_name`` exists."""
        ...

    def kill(self, session_name: str) -> None:
        """Terminate a session by name. No-op if it doesn't exist."""
        ...

    def list_sessions(self, prefix: str | None = None) -> list[str]:
        """Return session names known to this backend.

        If ``prefix`` is given, restrict to names starting with it. The
        runner uses this to filter to its own ``BackendConfig.session_prefix``
        when sharing a host with other tmux users.
        """
        ...


class BackendError(RuntimeError):
    """Raised by ``TerminalBackend`` implementations on multiplexer failure."""


__all__ = [
    "SpawnMode",
    "BackendConfig",
    "SpawnRequest",
    "AttachInfo",
    "SpawnResult",
    "TerminalBackend",
    "BackendError",
]
