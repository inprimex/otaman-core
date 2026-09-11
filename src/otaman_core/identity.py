"""Canonical agent-identity resolution for ownership ENFORCEMENT decisions.

F013 fix (security GAP finding, 2026-07-04): prior to this module, identity
for enforcement was resolved independently by three separately-maintained
implementations — otaman-cli's ``identity.py``, otaman-plugin's
``_resolve.sh`` PreToolUse hook, and otaman-plugin's ``bus_server.py`` MCP
tool — all reimplementing the same priority chain. That drift already caused
a real incident (the MCP resolver misattributing every ``otaman_send`` call
to ``plugin-agent`` regardless of actual caller, 2026-06-08). Going forward,
CLI, hooks, and MCP tools should all resolve enforcement-grade identity
through this module (directly in Python, or via a thin CLI wrapper for
non-Python callers like the Bash hook) instead of re-deriving the chain.

This is deliberately **narrower** than the general identity-resolution chain
used for CLI convenience/display (``otaman whoami``, explicit ``--agent``
flags, the ``OTAMAN_AGENT`` env var) — enforcement decisions ("can this
session write to this path?") must not trust signals any agent's own tool
calls can freely set. Two signals are excluded here even though they remain
valid for display elsewhere:

- ``OTAMAN_AGENT`` environment variable — an ordinary process env var; an
  agent's own Bash tool call can set it to claim any identity.
- ``.agents/current-agent`` — a single global mutable file shared across all
  concurrent agent sessions in the project; any session can overwrite it,
  and its value says nothing about which session/repo is actually asking.

Only the per-directory ``.otaman`` ``agent:`` marker (see
:func:`otaman_core._resolve.read_agent`) is honored here. It is still
self-asserted text — a session with write access to a directory could plant
a false marker there — but it is scoped to a specific repo path rather than
global process state, and planting a false one requires the agent to have
already written into that specific directory. This narrows the spoofing
surface; it does not eliminate it. True unforgeability would need identity
bound to which repo/worktree a session actually launched into, at the
Claude Code harness/session-launch level — out of scope for this module,
flagged as a follow-on.

Every enforcement resolution is appended to a JSONL audit log at
``<otaman-root>/.agents/audit/identity-resolutions.jsonl`` so spoofing
attempts are at least detectable after the fact, even where not prevented.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from otaman_core._resolve import find_maestro_root, read_agent
from otaman_core.owner_paths import load_platform_config, resolve_owner_for_cwd

_AUDIT_LOG_RELATIVE_PATH = Path(".agents") / "audit" / "identity-resolutions.jsonl"


@dataclass(frozen=True)
class EnforcementIdentity:
    """Result of an enforcement-grade identity resolution."""

    agent: str | None  # resolved agent name, or None if unresolved
    source: str  # "dotoman-marker" | "unresolved"
    cwd: str  # the directory the resolution was performed from


def resolve_enforcement_identity(cwd: Path | None = None) -> EnforcementIdentity:
    """Resolve agent identity for an ownership-enforcement decision.

    See the module docstring for what is and is not trusted here. Always
    appends the resolution outcome to the audit trail before returning,
    regardless of whether an identity was found.
    """
    resolved_cwd = (cwd or Path.cwd()).resolve()
    agent = read_agent(resolved_cwd)
    result = EnforcementIdentity(
        agent=agent,
        source="dotoman-marker" if agent else "unresolved",
        cwd=str(resolved_cwd),
    )
    _append_audit_entry(result)
    return result


def _append_audit_entry(result: EnforcementIdentity) -> None:
    """Best-effort append to the identity-resolution audit log.

    Failures to write the audit log (read-only filesystem, no otaman root
    found, etc.) must never block the caller's enforcement decision — this
    is a detective control, not a preventive one.
    """
    root = find_maestro_root(Path(result.cwd))
    if root is None:
        return
    audit_path = root / _AUDIT_LOG_RELATIVE_PATH
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "agent": result.agent,
            "source": result.source,
            "cwd": result.cwd,
        }
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def resolve_agent_identity(
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    project_root: Path | None = None,
) -> str | None:
    """Resolve the acting agent's DISPLAY / session identity (team-mode B1).

    The single shared session-identity resolver — one kernel implementation
    cli, bridge, plugin, and runner all consume (no per-repo copies, no F013
    drift). Two signals, with **cwd-ownership AUTHORITATIVE**:

    1. **cwd-ownership** via the platform.yaml ownership map
       (:func:`otaman_core.owner_paths.resolve_owner_for_cwd`). If ``cwd``
       resolves to an owner, that owner wins — even when ``OTAMAN_AGENT`` is set
       to something else. A poisoned or stale env var cannot override the real
       owner of the directory the session is actually in.
    2. **OTAMAN_AGENT** (process env), applied ONLY when ``cwd`` is not owned
       (unresolved) — process-scoped, subordinate to cwd-ownership.

    No ``.agents/current-agent`` and no ``.otaman`` marker reads — both retired
    by Roman's B1 amendment. Returns the agent name, or ``None`` when neither
    signal resolves (the caller decides what unresolved means).

    ``env`` defaults to ``os.environ``; inject a mapping for tests. ``project_root``
    (the directory containing platform.yaml) is auto-discovered from ``cwd`` when
    omitted. This is distinct from :func:`resolve_enforcement_identity`, which is
    marker-only and audited for ownership-ENFORCEMENT decisions.
    """
    resolved_cwd = (cwd or Path.cwd()).resolve()
    root = project_root or find_maestro_root(resolved_cwd)

    cwd_owner: str | None = None
    if root is not None:
        platform = load_platform_config(root / "platform.yaml")
        if platform is not None:
            cwd_owner = resolve_owner_for_cwd(platform, resolved_cwd, root)
    if cwd_owner:
        return cwd_owner

    environ = env if env is not None else os.environ
    env_agent = (environ.get("OTAMAN_AGENT") or "").strip()
    return env_agent or None
