"""The single identity-lock primitive for the acting session (single-acting-session-guard 0.1).

One acting session per resolved agent identity, enforced by ``flock(2)`` on an fd
the acting process holds open for its lifetime — the kernel releases it on ANY
exit (including ``kill -9``), so a crashed holder can never block a successor
(design D1/D3). Advisory mirrors (current-agent files, status yamls) are NOT the
lock; the flock is the truth.

This module is the SOLE implementation of the lock-file semantics. cli imports it
for the bus-write guard and the ``otaman acting-lock`` verbs; the bash launcher
only ever reaches it through cli's ``otaman acting-lock run`` wrapper (no flock
logic in bash).

Layout (design D1):
- lock:    ``$XDG_RUNTIME_DIR/otaman/<key>.lock``  (fallback ``~/.otaman/locks/``)
- key:     ``<org>--<program>--<agent>`` from the RESOLVED agent URI — program-
           scoped, because a bare agent name can run in two different programs.
- .info:   metadata sidecar beside the lock (``<lock>.info``): pid, mode
           (``interactive`` | ``background``), tmux session, started-at.
           Informational only — for errors/preemption/humans.
- preempt: cooperative-handoff marker beside the lock (``<lock>.preempt``):
           preemptor pid, mode, timestamp (design D3).

flock is POSIX-only; on platforms without ``fcntl`` the acquisition/probe calls
raise :class:`ActingLockError` (the pure path/key/marker helpers still work).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from otaman_core.bus import uri as _uri

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (Windows); flock unavailable
    fcntl = None  # type: ignore[assignment]

#: Acting modes an acquirer may declare.
LOCK_MODES: tuple[str, ...] = ("interactive", "background")


class ActingLockError(RuntimeError):
    """Raised on invalid input or when flock is unavailable on this platform."""


class ActingLockHeld(ActingLockError):
    """Raised by :func:`acquire` when the identity lock is already held.

    ``holder`` is the live holder's ``.info`` metadata (pid / mode / tmux
    session / started-at), or ``None`` if the sidecar is missing.
    """

    def __init__(self, key: str, holder: dict | None) -> None:
        self.key = key
        self.holder = holder
        pid = holder.get("pid") if holder else "unknown"
        session = holder.get("tmux_session") if holder else None
        where = f" in tmux {session}" if session else ""
        super().__init__(f"acting lock {key!r} is held by pid {pid}{where}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def lock_key(resolved_uri: str) -> str:
    """``otaman://<org>/<program>/<agent>`` → ``<org>--<program>--<agent>``.

    Requires a RESOLVED (full) URI — the caller resolves shorthand/bare forms
    first (via :mod:`otaman_core.bus.uri`). Raises :class:`ActingLockError` on a
    non-full or malformed URI.
    """
    if not isinstance(resolved_uri, str) or not resolved_uri.startswith(_uri.SCHEME):
        raise ActingLockError(
            f"lock_key requires a full {_uri.SCHEME}<org>/<program>/<agent> URI, "
            f"got {resolved_uri!r}"
        )
    try:
        bu = _uri.parse(resolved_uri, local_org="", local_program="")
    except _uri.BusUriError as exc:
        raise ActingLockError(f"invalid acting-lock URI {resolved_uri!r}: {exc}") from exc
    return f"{bu.org}--{bu.program}--{bu.agent}"


def locks_dir(*, runtime_dir: str | Path | None = None, home: str | Path | None = None) -> Path:
    """The lock directory: ``$XDG_RUNTIME_DIR/otaman`` or ``~/.otaman/locks``.

    ``runtime_dir`` / ``home`` are injectable for tests; production reads
    ``XDG_RUNTIME_DIR`` and falls back to the home dir when it is absent.
    """
    xdg = runtime_dir if runtime_dir is not None else os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "otaman"
    base = home if home is not None else Path.home()
    return Path(base) / ".otaman" / "locks"


def _key_of(target: str) -> str:
    return lock_key(target) if target.startswith(_uri.SCHEME) else target


def lock_path(
    target: str,
    *,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> Path:
    """Lock file path for a full URI or an already-computed ``<org>--...`` key."""
    return locks_dir(runtime_dir=runtime_dir, home=home) / f"{_key_of(target)}.lock"


def _info_path(lock: Path) -> Path:
    return Path(f"{lock}.info")


def _preempt_path(lock: Path) -> Path:
    return Path(f"{lock}.preempt")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{path}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class ActingLock:
    """A held identity lock. The kernel releases the flock when ``_fd`` closes —
    on :meth:`release`, context-manager exit, or process death (incl. ``kill -9``).
    """

    key: str
    path: Path
    mode: str
    _fd: int
    _released: bool = False

    @property
    def info_path(self) -> Path:
        return _info_path(self.path)

    def release(self) -> None:
        """Close the fd (kernel releases the flock) and best-effort clear the
        ``.info``/``.preempt`` sidecars. Idempotent."""
        if self._released:
            return
        os.close(self._fd)
        self._released = True
        for sidecar in (_info_path(self.path), _preempt_path(self.path)):
            try:
                sidecar.unlink()
            except OSError:
                pass

    def __enter__(self) -> ActingLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def _require_fcntl() -> None:
    if fcntl is None:
        raise ActingLockError("flock-based acting lock requires POSIX (fcntl unavailable)")


def acquire(
    target: str,
    *,
    mode: str,
    tmux_session: str | None = None,
    pid: int | None = None,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> ActingLock:
    """Acquire the identity lock non-blocking; returns a held :class:`ActingLock`.

    On contention raises :class:`ActingLockHeld` (carrying the live holder's
    metadata). On success writes the ``.info`` sidecar and returns a handle that
    holds the fd open — releasing (or process exit) frees the lock.

    ``target`` is a full URI or a precomputed lock key. ``mode`` must be one of
    :data:`LOCK_MODES`.
    """
    _require_fcntl()
    if mode not in LOCK_MODES:
        raise ActingLockError(f"mode must be one of {', '.join(LOCK_MODES)}, got {mode!r}")
    key = _key_of(target)
    path = lock_path(key, runtime_dir=runtime_dir, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]  # POSIX-only
    except OSError as exc:
        os.close(fd)
        raise ActingLockHeld(key, holder=_read_json(_info_path(path))) from exc

    _write_json(
        _info_path(path),
        {
            "pid": pid if pid is not None else os.getpid(),
            "mode": mode,
            "tmux_session": tmux_session,
            "started_at": _now(),
        },
    )
    return ActingLock(key=key, path=path, mode=mode, _fd=fd)


def holder_info(
    target: str,
    *,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> dict | None:
    """The lock's ``.info`` metadata (pid/mode/tmux/started-at), or ``None``.

    This reads the sidecar only — it does NOT prove a live holder (the sidecar
    can be stale after a crash). Use :func:`probe` for liveness.
    """
    return _read_json(_info_path(lock_path(target, runtime_dir=runtime_dir, home=home)))


def holder_pid(
    target: str,
    *,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> int | None:
    """The holder pid from the ``.info`` sidecar, or ``None``."""
    info = holder_info(target, runtime_dir=runtime_dir, home=home)
    pid = info.get("pid") if info else None
    return pid if isinstance(pid, int) else None


def probe(
    target: str,
    *,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> dict | None:
    """Liveness probe: the holder's ``.info`` if the lock is currently held, else
    ``None``.

    Determines held-ness by a non-blocking flock attempt (the truth), so a stale
    ``.info`` from a crashed holder reads as free. Never steals the lock — it
    releases immediately if it managed to take it.
    """
    _require_fcntl()
    path = lock_path(target, runtime_dir=runtime_dir, home=home)
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]  # POSIX-only
    except OSError:
        return _read_json(_info_path(path))  # held by someone live
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]  # POSIX-only
        return None
    finally:
        os.close(fd)


def write_preempt_marker(
    target: str,
    *,
    pid: int,
    mode: str,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> Path:
    """Write the cooperative-handoff ``.preempt`` marker beside the lock (D3).

    An interactive preemptor drops this; the background holder observes it and
    demotes (stops acting, releases by exit). Returns the marker path.
    """
    if mode not in LOCK_MODES:
        raise ActingLockError(f"mode must be one of {', '.join(LOCK_MODES)}, got {mode!r}")
    path = _preempt_path(lock_path(target, runtime_dir=runtime_dir, home=home))
    _write_json(path, {"pid": pid, "mode": mode, "timestamp": _now()})
    return path


def read_preempt_marker(
    target: str,
    *,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> dict | None:
    """Read the ``.preempt`` marker (pid/mode/timestamp), or ``None`` if absent."""
    return _read_json(_preempt_path(lock_path(target, runtime_dir=runtime_dir, home=home)))


def clear_preempt_marker(
    target: str,
    *,
    runtime_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> None:
    """Remove the ``.preempt`` marker if present (idempotent)."""
    try:
        _preempt_path(lock_path(target, runtime_dir=runtime_dir, home=home)).unlink()
    except OSError:
        pass


__all__ = [
    "LOCK_MODES",
    "ActingLock",
    "ActingLockError",
    "ActingLockHeld",
    "acquire",
    "clear_preempt_marker",
    "holder_info",
    "holder_pid",
    "lock_key",
    "lock_path",
    "locks_dir",
    "probe",
    "read_preempt_marker",
    "write_preempt_marker",
]
