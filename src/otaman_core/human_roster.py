#!/usr/bin/env python3
"""human-roster — typed view of the ``human-roster:`` block in platform.yaml.

Defines the data shape consumed by the bridge's role-to-assignee resolution
algorithm (`resolve_assignee`) and by `otaman pm init --roster` for
PM-user-id auto-resolution.

Pattern mirrors ``pm_sync.py``: a dataclass + a module-level loader that reads
platform.yaml and returns typed objects. Hyphenated YAML keys (``pm-user-id``)
are accepted alongside their underscored equivalents.

Typical platform.yaml shape::

    human-roster:
      - name: Jane Doe
        email: dev@otaman.ai
        roles: [cofounder, cto, cpo]
        pm-user-id: 1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HumanRosterEntry:
    """One person in the human-roster.

    ``pm_user_id`` is None until resolved by ``otaman pm init --roster``.
    ``roles`` is a non-empty list of role tags (e.g., 'cofounder', 'cto',
    'cpo', 'developer'). The loader rejects empty roles.
    """

    name: str
    email: str
    roles: list[str] = field(default_factory=list)
    pm_user_id: int | None = None


class ConfigError(ValueError):
    """Raised when platform.yaml contains a structurally invalid section.

    Distinct from :class:`jsonschema.ValidationError` (which is raised by
    `validate_platform`) because this captures semantic constraints that
    JSON Schema can't express cleanly — e.g., "roles must be non-empty for
    every roster entry, referenced by name in the error message".
    """


def _coerce_entry(raw: Any, index: int) -> HumanRosterEntry:
    """Build one :class:`HumanRosterEntry` from a raw YAML mapping.

    Validates the per-entry shape and raises :class:`ConfigError` with a
    message that references the offending entry's name (or its list index
    when ``name:`` itself is missing) so users can find the row quickly.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"human-roster[{index}]: expected a mapping, got {type(raw).__name__}")

    name = raw.get("name")
    email = raw.get("email")
    # Accept hyphenated YAML key plus underscored alias (mirrors pm_sync).
    pm_user_id_raw = raw.get("pm-user-id")
    if pm_user_id_raw is None:
        pm_user_id_raw = raw.get("pm_user_id")
    roles_raw = raw.get("roles")

    # Human-readable identifier for error messages.
    label = repr(name) if name else f"index {index}"

    if not isinstance(name, str) or not name:
        raise ConfigError(
            f"human-roster[{index}]: 'name' is required and must be a non-empty string"
        )
    if not isinstance(email, str) or not email:
        raise ConfigError(
            f"human-roster entry {label}: 'email' is required and must be a non-empty string"
        )
    if not isinstance(roles_raw, list):
        raise ConfigError(
            f"human-roster entry {label}: 'roles' must be a list, got {type(roles_raw).__name__}"
        )
    if len(roles_raw) == 0:
        raise ConfigError(f"human-roster entry {label}: 'roles' must be a non-empty list")
    if not all(isinstance(r, str) and r for r in roles_raw):
        raise ConfigError(
            f"human-roster entry {label}: every value in 'roles' must be a non-empty string"
        )

    pm_user_id: int | None
    if pm_user_id_raw is None:
        pm_user_id = None
    elif isinstance(pm_user_id_raw, bool) or not isinstance(pm_user_id_raw, int):
        # Bools satisfy `isinstance(_, int)` in Python; reject explicitly.
        raise ConfigError(
            f"human-roster entry {label}: 'pm-user-id' must be an integer when present"
        )
    else:
        pm_user_id = pm_user_id_raw

    return HumanRosterEntry(
        name=name,
        email=email,
        roles=list(roles_raw),
        pm_user_id=pm_user_id,
    )


def parse_human_roster(block: Any) -> list[HumanRosterEntry]:
    """Parse the ``human-roster:`` block content.

    ``block`` is the value read from platform.yaml under the
    ``human-roster:`` key (usually a list of mappings). Absent / null /
    empty list all yield an empty result without error.

    Raises :class:`ConfigError` if any entry is structurally invalid (per
    the rules in :func:`_coerce_entry`).
    """
    if block is None:
        return []
    if not isinstance(block, list):
        raise ConfigError(f"human-roster: expected a list of entries, got {type(block).__name__}")
    return [_coerce_entry(entry, i) for i, entry in enumerate(block)]


def load_human_roster(platform_yaml_path: Path) -> list[HumanRosterEntry]:
    """Read platform.yaml and return the parsed roster.

    Missing file or absent block yield an empty list. YAML parse errors
    yield an empty list (caller treats "no roster" as "no PM assignee
    resolution"). Structural errors in present entries raise
    :class:`ConfigError` so they surface during ``otaman doctor`` /
    ``otaman init`` rather than failing silently at issue-creation time.
    """
    import yaml  # local import keeps the module yaml-optional at import time

    if not platform_yaml_path.is_file():
        return []
    try:
        data = yaml.safe_load(platform_yaml_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []

    return parse_human_roster(data.get("human-roster"))


__all__ = [
    "ConfigError",
    "HumanRosterEntry",
    "parse_human_roster",
    "load_human_roster",
]
