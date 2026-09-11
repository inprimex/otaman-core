"""Per-human account → config_dir resolution (team-mode 2.3a).

Reads the org's ``launch-settings.yaml`` ``accounts:`` block and resolves the
Claude config directory (``CLAUDE_CONFIG_DIR``) for an acting human, so a
multi-human tenant can give each person their own user-level ``~/.claude``
remainder (Mode-1 per-human memory split — separation, not privacy).

This is the ONE kernel parser of ``launch-settings.yaml`` accounts (spec-agent
ruling 2026-09-11, mirroring the B1 identity-resolver kernel pattern): the
runner consumer (v0.2.11) and the launcher scripts both call it, so the file is
never parsed twice with drifting semantics. Values-free — it returns a path
locator, never account credentials.

Account ↔ human mapping is the explicit ``human:`` field on each
``accounts.<name>`` entry (an email or roster id); account NAMES stay
OAuth-profile-routing names and are never overloaded as human ids. cli owns the
``human:`` schema addition. Absent accounts block, unmapped human, or missing
``config_dir`` all resolve to ``None`` — the caller (runner) then loudly warns
and falls back to the shared default; it never fails.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def launch_settings_path(org_root: Path) -> Path:
    """The org launch-settings file: ``<org_root>/launch-settings.yaml``."""
    return org_root / "launch-settings.yaml"


def _load_accounts(org_root: Path) -> dict[str, Any]:
    """Return the ``accounts:`` mapping from launch-settings.yaml (``{}`` if absent)."""
    import yaml  # local import keeps the module yaml-optional at import time

    path = launch_settings_path(org_root)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return accounts if isinstance(accounts, dict) else {}


def _expand(config_dir: str) -> Path:
    """Expand ``~`` and ``$VARS`` in a config_dir string to a Path (not resolved)."""
    return Path(os.path.expandvars(os.path.expanduser(config_dir)))


def resolve_human_config_dir(org_root: Path, human_sub: str) -> Path | None:
    """Resolve the ``CLAUDE_CONFIG_DIR`` for an acting human (team-mode 2.3a).

    Reads ``<org_root>/launch-settings.yaml`` ``accounts:`` and returns the
    expanded ``config_dir`` of the account whose ``human:`` field matches
    ``human_sub`` (case-insensitively — the field is an email or roster id).
    Returns ``None`` when: the accounts block is absent, no account maps to
    ``human_sub``, or the matched account carries no ``config_dir``. The caller
    treats ``None`` as "use the shared default, loudly" — this never raises.

    The ``~`` / ``$VAR`` in ``config_dir`` are expanded; the path is returned
    whether or not it exists (the caller decides). Values-free: a directory
    locator only, never credentials.
    """
    sub = (human_sub or "").strip().lower()
    if not sub:
        return None
    for account in _load_accounts(org_root).values():
        if not isinstance(account, dict):
            continue
        human = account.get("human")
        if not (isinstance(human, str) and human.strip().lower() == sub):
            continue
        config_dir = account.get("config_dir")
        if isinstance(config_dir, str) and config_dir.strip():
            return _expand(config_dir)
        return None  # matched the human but no config_dir declared
    return None


__all__ = [
    "launch_settings_path",
    "resolve_human_config_dir",
]
