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

from otaman_core.human_roster import HumanRosterEntry


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


def resolve_human_config_dir(
    org_root: Path,
    human_sub: str,
    *,
    roster: list[HumanRosterEntry] | None = None,
) -> Path | None:
    """Resolve the ``CLAUDE_CONFIG_DIR`` for an acting human (team-mode 2.3a).

    Reads ``<org_root>/launch-settings.yaml`` ``accounts:`` and returns the
    expanded ``config_dir`` of the account whose ``human:`` field identifies the
    acting human. Matching:

    - ``roster`` given (the program's ``load_human_roster`` result): ROSTER
      EQUIVALENCE — the account matches when its ``human:`` ref resolves to the
      SAME roster entry as ``human_sub`` via
      :func:`~otaman_core.human_roster.resolve_roster_human` (so ``roman``,
      ``roman@x.com``, and the name-slug all match one person). If ``human_sub``
      does not resolve in this roster, the answer is ``None`` — correct, not a
      bug (see the org-vs-program note below).
    - ``roster`` omitted: case-insensitive exact string match on the ``human:``
      field — the safe interim (a hit works; a miss returns ``None``, never
      another person's dir).

    ORG-vs-PROGRAM SCOPING (do not memoize across programs): the account→human
    mapping is ORG-level (one ``launch-settings.yaml``), but roster equivalence
    is PROGRAM-level (``platform.yaml`` is per-program). The same
    ``human: "roman@x.com"`` can legitimately resolve in program A and not in
    program B whose roster lacks that person — so a result (including ``None``)
    is only meaningful relative to the ``roster`` handed in, and must never be
    cached org-wide. The caller passes the roster it loaded for the acting
    program's ``platform.yaml``.

    Returns ``None`` when the accounts block is absent, no account maps to the
    human, or the matched account has no ``config_dir``. The caller treats
    ``None`` as "use the shared default, loudly" — this never raises. ``~`` /
    ``$VAR`` are expanded; the path is returned whether or not it exists.
    Values-free: a directory locator only, never credentials.
    """
    accounts = _load_accounts(org_root)

    if roster is not None:
        from otaman_core.human_roster import resolve_roster_human

        target = resolve_roster_human(roster, human_sub)
        if target is None:
            return None  # acting human not in this program's roster
        for account in accounts.values():
            if not isinstance(account, dict):
                continue
            if resolve_roster_human(roster, account.get("human")) == target:
                return _config_dir_of(account)
        return None

    sub = (human_sub or "").strip().lower()
    if not sub:
        return None
    for account in accounts.values():
        if not isinstance(account, dict):
            continue
        human = account.get("human")
        if isinstance(human, str) and human.strip().lower() == sub:
            return _config_dir_of(account)
    return None


def _config_dir_of(account: dict[str, Any]) -> Path | None:
    """The expanded ``config_dir`` of a matched account, or ``None`` if unset."""
    config_dir = account.get("config_dir")
    if isinstance(config_dir, str) and config_dir.strip():
        return _expand(config_dir)
    return None


__all__ = [
    "launch_settings_path",
    "resolve_human_config_dir",
]
