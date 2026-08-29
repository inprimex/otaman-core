"""Program lifecycle registry — the org-level source of truth for program state.

Each org has a lifecycle registry at ``~/orgs/<org>/config/lifecycle.yaml``
mapping program name to its current ``{state, since, by, reason?}`` plus an
append-only ``history`` of every transition. State is one of
``active | limited | suspended | archived``; an absent entry OR an absent file
means ``active`` (program-lifecycle-states D1).

This module is the ONE lifecycle read point for every per-org service — runner,
bridge, fswatch, router, launcher, CLI all resolve state through
:func:`read_program_state` / :func:`load_lifecycle`, so there is no second
notion of "is this program running". The registry deliberately outlives the
program folder that ``archived`` moves away, so archived state stays readable.

``by`` is always the resolved roster human that authorized the transition
(never a bare OS account) — the writer records it; resolving it is the caller's
job (see :func:`otaman_core.human_roster.resolve_roster_human`).

This module owns 1.1 (model/loader/writer/validation/read-helper) and 1.2
(doctor checks). Runtime enforcement of each state lives in the consuming
services (step 2), not here.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from otaman_core.human_roster import DoctorFinding

#: Valid lifecycle states, broadest capability first. Absent == ``active``.
LIFECYCLE_STATES: tuple[str, ...] = ("active", "limited", "suspended", "archived")
DEFAULT_STATE = "active"

REGISTRY_FILENAME = "lifecycle.yaml"


class LifecycleError(ValueError):
    """Raised when lifecycle.yaml is structurally invalid or a state is unknown."""


@dataclass(frozen=True)
class LifecycleRecord:
    """One transition in a program's history. ``by`` is a resolved roster human."""

    state: str
    since: str  # ISO-8601
    by: str
    reason: str | None = None


@dataclass(frozen=True)
class LifecycleEntry:
    """A program's current lifecycle state plus its full transition history.

    The current ``state``/``since``/``by``/``reason`` mirror ``history[-1]``.
    """

    program: str
    state: str
    since: str
    by: str
    reason: str | None = None
    history: tuple[LifecycleRecord, ...] = field(default_factory=tuple)


def lifecycle_registry_path(org_root: Path) -> Path:
    """The registry path for an org root: ``<org_root>/config/lifecycle.yaml``."""
    return org_root / "config" / REGISTRY_FILENAME


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _coerce_record(raw: Any, program: str) -> LifecycleRecord:
    if not isinstance(raw, dict):
        raise LifecycleError(f"lifecycle entry {program!r}: history item must be a mapping")
    state = raw.get("state")
    if state not in LIFECYCLE_STATES:
        raise LifecycleError(
            f"lifecycle entry {program!r}: state {state!r} must be one of "
            f"{', '.join(LIFECYCLE_STATES)}"
        )
    reason = raw.get("reason")
    return LifecycleRecord(
        state=state,
        since=str(raw.get("since", "")),
        by=str(raw.get("by", "")),
        reason=str(reason) if reason is not None else None,
    )


def _coerce_entry(program: str, raw: Any) -> LifecycleEntry:
    if not isinstance(raw, dict):
        raise LifecycleError(f"lifecycle entry {program!r}: expected a mapping")
    state = raw.get("state")
    if state not in LIFECYCLE_STATES:
        raise LifecycleError(
            f"lifecycle entry {program!r}: state {state!r} must be one of "
            f"{', '.join(LIFECYCLE_STATES)}"
        )
    history_raw = raw.get("history", [])
    if not isinstance(history_raw, list):
        raise LifecycleError(f"lifecycle entry {program!r}: 'history' must be a list")
    history = tuple(_coerce_record(h, program) for h in history_raw)
    reason = raw.get("reason")
    return LifecycleEntry(
        program=program,
        state=state,
        since=str(raw.get("since", "")),
        by=str(raw.get("by", "")),
        reason=str(reason) if reason is not None else None,
        history=history,
    )


def parse_lifecycle(data: Any) -> dict[str, LifecycleEntry]:
    """Parse a lifecycle.yaml mapping into ``{program: LifecycleEntry}``.

    Absent / null / empty all yield ``{}``. Raises :class:`LifecycleError` on a
    malformed structure or an unknown state.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LifecycleError("lifecycle.yaml must be a mapping")
    programs = data.get("programs")
    if programs is None:
        return {}
    if not isinstance(programs, dict):
        raise LifecycleError("lifecycle.yaml 'programs' must be a mapping")
    return {name: _coerce_entry(name, entry) for name, entry in programs.items()}


def load_lifecycle(path: Path) -> dict[str, LifecycleEntry]:
    """Load the registry at ``path``. Absent file yields ``{}`` (all active).

    A YAML parse error also yields ``{}`` (treat an unreadable registry as "no
    overrides / active") rather than crashing every service; structural errors
    in a present, parseable file raise :class:`LifecycleError` so they surface
    in ``otaman doctor``.
    """
    import yaml

    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return parse_lifecycle(data)


def get_state(registry: Mapping[str, LifecycleEntry], program: str) -> str:
    """Current state of ``program`` in a loaded registry; absent entry → active."""
    entry = registry.get(program)
    return entry.state if entry is not None else DEFAULT_STATE


def read_program_state(org_root: Path, program: str) -> str:
    """THE lifecycle read point for services: load + resolve one program's state.

    Absent registry / absent entry → ``active``. Every per-org service (runner,
    bridge, fswatch, router, launcher, CLI) SHALL resolve state through this so
    there is exactly one notion of program lifecycle.
    """
    return get_state(load_lifecycle(lifecycle_registry_path(org_root)), program)


def _entry_to_yaml(entry: LifecycleEntry) -> dict[str, Any]:
    out: dict[str, Any] = {"state": entry.state, "since": entry.since, "by": entry.by}
    if entry.reason is not None:
        out["reason"] = entry.reason
    out["history"] = [
        {
            k: v
            for k, v in {"state": r.state, "since": r.since, "by": r.by, "reason": r.reason}.items()
            if v is not None
        }
        for r in entry.history
    ]
    return out


def record_transition(
    path: Path,
    program: str,
    state: str,
    *,
    by: str,
    reason: str | None = None,
    now: Callable[[], str] | None = None,
) -> LifecycleEntry:
    """Append a transition for ``program`` and persist; returns the new entry.

    History is append-only: the new ``{state, since, by, reason?}`` record is
    added to the program's history and becomes the current state. Other programs
    are preserved. ``by`` must be a non-empty resolved roster human (the caller
    resolves it; this layer records it). Atomic write, 0644 (org-user-owned,
    world-readable — services read it), parent dir created.

    Raises :class:`LifecycleError` on an unknown state or empty ``by``.
    """
    if state not in LIFECYCLE_STATES:
        raise LifecycleError(
            f"unknown lifecycle state {state!r}; expected one of {LIFECYCLE_STATES}"
        )
    if not by or not by.strip():
        raise LifecycleError("lifecycle transition requires 'by' (the resolved roster human)")

    clock = now or _now_iso
    registry = load_lifecycle(path)
    prior = registry.get(program)
    prior_history = prior.history if prior is not None else ()

    record = LifecycleRecord(state=state, since=clock(), by=by.strip(), reason=reason)
    entry = LifecycleEntry(
        program=program,
        state=record.state,
        since=record.since,
        by=record.by,
        reason=record.reason,
        history=(*prior_history, record),
    )
    registry[program] = entry

    _write_registry(path, registry)
    return entry


def _write_registry(path: Path, registry: Mapping[str, LifecycleEntry]) -> None:
    import yaml

    payload = {"programs": {name: _entry_to_yaml(e) for name, e in sorted(registry.items())}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o644)
    except OSError:  # pragma: no cover - non-POSIX
        pass
    os.replace(tmp, path)


# --- doctor checks (program-lifecycle-states 1.2) ---


def check_lifecycle(
    registry: Mapping[str, LifecycleEntry],
    *,
    live_programs: Iterable[str] = (),
    folder_present: Mapping[str, bool] | None = None,
) -> list[DoctorFinding]:
    """Advisory lifecycle doctor checks.

    - WARN a live session belonging to a non-``active`` program (advisory
      coverage for runner-bypassing entry points): for each name in
      ``live_programs`` whose registry state is not ``active``.
    - WARN a state-vs-folder contradiction when ``folder_present`` (a
      ``{program: bool}`` map the caller builds by checking the org tree) is
      given: ``archived`` but the folder is still present, or non-``archived``
      but the folder is missing.

    Returns findings in a stable order; empty when healthy.
    """
    findings: list[DoctorFinding] = []

    for program in sorted(set(live_programs)):
        state = get_state(registry, program)
        if state != DEFAULT_STATE:
            findings.append(
                DoctorFinding(
                    "warn",
                    f"program {program!r} is '{state}' but has a live session — "
                    "it should have no open sessions; check for a runner-bypassing entry point",
                )
            )

    if folder_present is not None:
        for program in sorted(registry):
            state = registry[program].state
            present = folder_present.get(program)
            if present is None:
                continue
            if state == "archived" and present:
                findings.append(
                    DoctorFinding(
                        "warn",
                        f"program {program!r} is 'archived' but its folder is still present — "
                        "archive should have moved it to the org archive",
                    )
                )
            elif state != "archived" and not present:
                findings.append(
                    DoctorFinding(
                        "warn",
                        f"program {program!r} is '{state}' but its folder is missing — "
                        "state and on-disk layout disagree",
                    )
                )

    return findings


__all__ = [
    "DEFAULT_STATE",
    "LIFECYCLE_STATES",
    "REGISTRY_FILENAME",
    "DoctorFinding",
    "LifecycleEntry",
    "LifecycleError",
    "LifecycleRecord",
    "check_lifecycle",
    "get_state",
    "lifecycle_registry_path",
    "load_lifecycle",
    "parse_lifecycle",
    "read_program_state",
    "record_transition",
]
