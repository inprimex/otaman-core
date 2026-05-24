"""Shared otaman workspace root resolution for all Python scripts.

Resolution chain (first match wins):
1. .otaman (preferred) or .maestro (legacy: removed at 1.0) marker file in start dir or ancestors
2. OTAMAN_ROOT (preferred) or MAESTRO_ROOT (legacy: removed at 1.0) environment variable
3. Walk-up fallback: look for platform.yaml or .agents/ (legacy/monorepo compat)

Also exposes expand_config_dir() for per-shell tilde / env-var expansion of
account config_dir paths declared in launch-settings.yaml.
"""

from __future__ import annotations

import os
import warnings as _warnings
from pathlib import Path

# Shells that resolve paths on a different host (remote / different userspace)
# from the Python interpreter. For these, we emit POSIX-style paths and defer
# tilde / env expansion to the target shell.
_DEFERRED_SHELLS = frozenset({"wsl", "ssh"})

# Shells that speak native Windows paths.
_WINDOWS_SHELLS = frozenset({"powershell", "pwsh", "cmd"})

# Known fields in marker files. Unknown `key:` lines fall through to
# bare-path handling, which preserves support for Windows absolute paths
# (e.g. ``C:/work/my-otaman``) that happen to contain a colon.
_KNOWN_MARKER_FIELDS = frozenset({"otaman_root", "maestro_root", "expected_account"})  # legacy: maestro_root retained for one minor release

# Keys emitted at most once per interpreter process, keyed by a unique channel string.
_warned: set[str] = set()


def _warn_once(key: str, message: str, category: type = DeprecationWarning) -> None:
    """Emit *category* warning with *message* at most once per process per *key*."""
    if key not in _warned:
        _warned.add(key)
        _warnings.warn(message, category, stacklevel=3)


def _has_explicit_maestro_root(marker_path: Path) -> bool:
    """Return True if marker file has an explicit ``maestro_root:`` key line.

    Distinguishes intentional ``maestro_root: <path>`` key usage from bare-path
    lines (which are silently mapped to the ``maestro_root`` key internally).
    """
    try:
        text = marker_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.strip().startswith("maestro_root:") for line in text.splitlines())


def _safe_marker_path(rel: str, marker: Path) -> bool:
    """Return False and warn if *rel* from *marker* is unsafe.

    Rejects paths with more than 3 ``..`` components (traversal bound) or that
    resolve to a location outside the user's home directory.
    """
    dotdot_count = sum(1 for p in Path(rel).parts if p == "..")
    if dotdot_count > 3:
        _warn_once(
            f"traversal:{marker}:{rel}",
            f"Marker at {marker} contains path with {dotdot_count} '..' levels: {rel!r}; rejected for security",
            UserWarning,
        )
        return False
    candidate = (marker.parent / rel).resolve()
    home = Path.home()
    try:
        candidate.relative_to(home)
    except ValueError:
        _warn_once(
            f"outside-home:{marker}:{rel}",
            f"Marker at {marker} resolves outside $HOME ({home}): {candidate}; rejected for security",
            UserWarning,
        )
        return False
    return True


def find_maestro_root(start: Path | None = None) -> Path | None:  # legacy: renamed find_otaman_root at 1.0
    """Find the otaman workspace root directory.

    Tries the standard resolution chain (marker file → OTAMAN_ROOT/
    MAESTRO_ROOT env → walk-up) from the given path. If that fails and
    the path is inside a linked git worktree, retries from the worktree's
    main repo — where ``.otaman`` / ``.maestro`` (legacy: removed at 1.0) markers live.

    Args:
        start: Directory to start searching from. Defaults to cwd.

    Returns:
        Resolved absolute path to the otaman root, or None if not found.
    """
    origin = (start or Path.cwd()).resolve()

    direct = _find_maestro_root_from(origin)
    if direct is not None:
        return direct

    # Worktrees don't usually carry their own marker; resolve to the
    # main repo's working tree and retry the chain from there.
    main = resolve_worktree_main(origin)
    if main is not None:
        return _find_maestro_root_from(main)

    return None


def _find_maestro_root_from(origin: Path) -> Path | None:  # legacy: renamed at 1.0
    """Run the marker → env → walk-up chain starting from ``origin``.

    Internal helper for :func:`find_maestro_root`. Returns the resolved
    otaman root or ``None`` on no match.
    """
    # 1. .otaman (preferred) or .maestro (legacy: removed at 1.0) marker file — walk up
    current = origin
    while current != current.parent:
        marker = current / ".otaman"
        if not marker.is_file():
            legacy_marker = current / ".maestro"  # legacy: .maestro fallback removed at 1.0
            if legacy_marker.is_file():
                _warn_once(
                    f"legacy-marker:{legacy_marker}",
                    f"Found legacy '.maestro' marker at {legacy_marker}; "  # legacy: deprecation warning for .maestro marker
                    "rename to '.otaman' before otaman-core 1.0",
                )
                marker = legacy_marker
        if marker.is_file():
            fields = parse_marker_fields(marker)
            rel = fields.get("otaman_root") or fields.get("maestro_root")  # legacy: maestro_root fallback
            if rel:
                if "otaman_root" not in fields and _has_explicit_maestro_root(marker):
                    _warn_once(
                        f"legacy-field:{marker}",
                        f"Marker at {marker} uses legacy 'maestro_root:' field; "  # legacy: maestro_root field deprecated
                        "rename to 'otaman_root:' before otaman-core 1.0",
                    )
                if not _safe_marker_path(rel, marker):
                    current = current.parent
                    continue
                candidate = (current / rel).resolve()
                if (candidate / "platform.yaml").exists() or (candidate / ".agents").is_dir():
                    return candidate
        current = current.parent

    # 2. OTAMAN_ROOT (preferred) or MAESTRO_ROOT (legacy: removed at 1.0) env variable
    otaman_env = os.environ.get("OTAMAN_ROOT", "").strip()
    maestro_env = os.environ.get("MAESTRO_ROOT", "").strip()  # legacy: MAESTRO_ROOT removed at 1.0
    if otaman_env and maestro_env:
        _warn_once(
            "maestro-root-ignored",  # legacy: internal key for MAESTRO_ROOT-ignored warning
            "MAESTRO_ROOT is set but OTAMAN_ROOT takes precedence; "  # legacy: warning for ignored MAESTRO_ROOT
            "MAESTRO_ROOT will be removed in otaman-core 1.0",
        )
        env_root = otaman_env
    elif maestro_env:
        _warn_once(
            "maestro-root-deprecated",  # legacy: internal key for MAESTRO_ROOT-deprecated warning
            "MAESTRO_ROOT is deprecated; set OTAMAN_ROOT instead. "  # legacy: warning for deprecated MAESTRO_ROOT
            "Will be removed in otaman-core 1.0",
        )
        env_root = maestro_env
    else:
        env_root = otaman_env
    if env_root:
        p = Path(env_root).resolve()
        if (p / "platform.yaml").exists() or (p / ".agents").is_dir():
            return p

    # 3. Walk-up fallback (legacy layout: otaman artifacts in a parent directory)
    current = origin
    while current != current.parent:
        if (current / "platform.yaml").exists() or (current / ".agents").is_dir():
            return current
        current = current.parent

    return None


def resolve_worktree_main(path: Path | None = None) -> Path | None:
    """If ``path`` is inside a git worktree, return the main repo's working tree.

    A linked worktree has a ``.git`` *file* (not directory) at its root. The
    file contains a single line ``gitdir: <path>`` pointing into the main
    repo's ``.git/worktrees/<name>/`` directory. From that gitdir we can
    recover the main repo's working tree as the great-grandparent.

    Walks up from ``path`` looking for any ``.git`` entry:

    - ``.git`` is a regular file → worktree marker, parse and resolve
    - ``.git`` is a directory     → ordinary repo (not a worktree)
    - ``.git`` not found          → not a git working tree at all

    Returns the absolute Path to the main repo's working tree, or ``None``
    when ``path`` is not inside a worktree (including when it's inside the
    main repo itself, or not in any repo). Defensive against malformed
    ``.git`` files: parse failures return ``None`` rather than raising.

    Used by :func:`find_maestro_root` so that hooks fired from inside a  # legacy: find_maestro_root renamed at 1.0
    linked worktree can still locate the otaman folder via the main
    repo's ``.otaman`` / ``.maestro`` marker.  # legacy: .maestro fallback removed at 1.0
    """
    try:
        origin = (path or Path.cwd()).resolve()
    except (OSError, ValueError):
        return None

    current = origin
    while current != current.parent:
        git_entry = current / ".git"
        if git_entry.is_file():
            try:
                text = git_entry.read_text(encoding="utf-8")
            except OSError:
                return None
            for raw in text.splitlines():
                line = raw.strip()
                if not line.startswith("gitdir:"):
                    continue
                gitdir = line.split(":", 1)[1].strip()
                if not gitdir:
                    return None
                gitdir_path = Path(gitdir)
                if not gitdir_path.is_absolute():
                    try:
                        gitdir_path = (current / gitdir_path).resolve()
                    except (OSError, ValueError):
                        return None
                # gitdir = <main_repo>/.git/worktrees/<name>
                # main repo working tree = <main_repo>
                try:
                    if (
                        gitdir_path.parent.name == "worktrees"
                        and gitdir_path.parent.parent.name == ".git"
                    ):
                        return gitdir_path.parent.parent.parent.resolve()
                except (OSError, ValueError):
                    return None
                return None
            return None
        if git_entry.is_dir():
            # Ordinary (non-worktree) repo. Caller should keep using ``path``.
            return None
        current = current.parent
    return None


def parse_marker_fields(marker_path: Path) -> dict[str, str]:
    """Parse a marker file into a dict of fields.

    Accepts two formats, chosen line-by-line:

    - **Legacy** — a single bare line holding the relative path to the
      otaman folder (e.g. ``../my-otaman``). Becomes ``maestro_root``
      (legacy: field renamed to ``otaman_root`` at 1.0).
    - **Extended** — ``key: value`` lines for known fields, plus an
      optional bare path line. Current known fields: ``otaman_root``,
      ``maestro_root`` (legacy: renamed at 1.0), ``expected_account``.

    Unknown ``key: value`` lines are ignored so that Windows absolute
    paths containing a colon (``C:/foo``) continue to parse as bare
    ``otaman_root`` / ``maestro_root`` values. Comment (``#``) and blank
    lines are skipped.
    """
    fields: dict[str, str] = {}
    try:
        text = marker_path.read_text(encoding="utf-8")
    except OSError:
        return fields
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key in _KNOWN_MARKER_FIELDS:
                fields.setdefault(key, value)
                continue
        # Bare line → treat as maestro_root if not set (legacy: bare path mapping removed at 1.0)
        fields.setdefault("maestro_root", line)  # legacy: bare path stored as maestro_root for compat
    return fields


def find_marker(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default: cwd) looking for an Otaman marker file.

    Prefers ``.otaman``; falls back to ``.maestro`` (legacy: removed at 1.0).
    Returns the marker path, or None if not found.
    """
    origin = (start or Path.cwd()).resolve()
    current = origin
    while current != current.parent:
        marker = current / ".otaman"
        if marker.is_file():
            return marker
        legacy = current / ".maestro"  # legacy: .maestro fallback removed at 1.0
        if legacy.is_file():
            return legacy
        current = current.parent
    return None


def read_expected_account(start: Path | None = None) -> str | None:
    """Return the ``expected_account`` field from the nearest Otaman marker.

    Checks ``.otaman`` (preferred) or ``.maestro`` (legacy: removed at 1.0).
    Returns None if no marker is found or the field is absent/empty.
    """
    marker = find_marker(start)
    if marker is None:
        return None
    value = parse_marker_fields(marker).get("expected_account")
    return value if value else None


def expand_config_dir(config_dir: str, shell: str, *, home: str | None = None) -> str:
    """Expand a ``config_dir`` spec for a target shell.

    The value comes from ``launch-settings.yaml accounts.<name>.config_dir``
    (e.g. ``~/.claude-personal``). Different shells expand tildes and env
    variables differently, and some shells (wsl, ssh) resolve on a different
    host entirely — in those cases we defer expansion to the target shell.

    Args:
        config_dir: Raw value from YAML. May contain ``~``, ``$HOME``,
            ``${HOME}``, ``$USERPROFILE``, ``${USERPROFILE}``.
        shell: Target shell name. Understood values:
            - ``powershell`` / ``pwsh`` / ``cmd`` — native Windows path output
            - ``bash`` / ``zsh`` / ``fish`` — POSIX-slash output, expanded
            - ``wsl`` / ``ssh`` — pass-through; target shell resolves
        home: Override for ``$HOME`` / ``~``. Defaults to ``Path.home()``.
            Mainly for tests and cross-shell expansion (e.g. a Windows
            launcher computing a WSL path without needing to shell out).

    Returns:
        A path string appropriate for the target shell. Empty input returns
        an empty string.
    """
    if not config_dir:
        return ""

    # Accept both forward and back slashes in the input.
    s = config_dir.replace("\\", "/")

    if shell in _DEFERRED_SHELLS:
        # Deferred: keep as-is (POSIX slashes already). Remote shell expands.
        return s

    # Local shell — expand env vars and tilde.
    resolved_home = home if home is not None else str(Path.home())
    resolved_home = resolved_home.replace("\\", "/")

    for token in ("${HOME}", "$HOME", "${USERPROFILE}", "$USERPROFILE"):
        s = s.replace(token, resolved_home)

    if s == "~":
        s = resolved_home
    elif s.startswith("~/"):
        s = f"{resolved_home}/{s[2:]}"

    if shell in _WINDOWS_SHELLS:
        return s.replace("/", "\\")
    return s.replace("\\", "/")


# --- Routing resolution (formerly "account", briefly "profile") -------------
# Routing name is the per-host identity scope used for Telegram routing,
# AFK state tagging, and bridge daemon endpoint files. Renamed from
# "account" 2026-05-09 (which was overloaded with provider-account
# vocabulary). Briefly renamed to "profile" but that collided with
# platform.yaml's existing profiles: block (repo-subset bundles), so
# settled on "routing". Legacy "account" name kept as fallback for one
# release window; sunset planned for otaman-core 1.0 alongside the
# .maestro marker dual-recognition.  # legacy: .maestro dual-recognition removed at 1.0

import os as _os


def active_routing_env() -> str | None:
    """Read the active routing name from environment.

    Resolution order (most preferred first):
      1. ``OTAMAN_ACTIVE_ROUTING`` — current name (set by launcher).
      2. ``OTAMAN_ACTIVE_ACCOUNT`` — pre-rename otaman legacy.
      3. ``MAESTRO_ACTIVE_ACCOUNT`` — pre-rebrand legacy.  # legacy: MAESTRO_ACTIVE_ACCOUNT removed at 1.0
    """
    return (
        _os.environ.get("OTAMAN_ACTIVE_ROUTING")
        or _os.environ.get("OTAMAN_ACTIVE_ACCOUNT")
        or _os.environ.get("MAESTRO_ACTIVE_ACCOUNT")  # legacy: MAESTRO_ACTIVE_ACCOUNT removed at 1.0
    )


def read_expected_routing(start: Path | None = None) -> str | None:
    """Read expected routing name from Otaman marker.

    Reads both new field name (``expected_routing:``) and legacy field
    (``expected_account:``); prefers new when both are present.
    """
    marker = find_marker(start)
    if not marker:
        return None
    fields = parse_marker_fields(marker)
    return fields.get("expected_routing") or fields.get("expected_account")
