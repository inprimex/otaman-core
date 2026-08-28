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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The well-known roster role that grants "may work with proposals" — approve
#: spec-change-requests and take HITL review requests (hitl-default-approver).
#: The single authoritative grant: neither ``terminal.users`` RBAC nor onboarding
#: labels confer it. The roster still accepts arbitrary additional role strings.
APPROVER_ROLE = "approver"


@dataclass(frozen=True)
class HumanRosterEntry:
    """One person in the human-roster.

    ``pm_user_id`` is None until resolved by ``otaman pm init --roster``.
    ``roles`` is a non-empty list of role tags (e.g., 'cofounder', 'cto',
    'cpo', 'developer', 'approver'). The loader rejects empty roles.
    ``email`` is optional: provisioning may enrol a day-one approver from an
    SSH-key comment that is not an email (``otaman doctor`` then WARNs), so a
    live approval path beats a dead one (hitl-default-approver D3).
    """

    name: str
    email: str | None = None
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
    # ``email`` is OPTIONAL (hitl-default-approver D3): omitted -> None. When
    # present it must be a non-empty string. ``otaman doctor`` WARNs on an
    # approver entry with no email; provisioning never fails for want of one.
    if email is not None and (not isinstance(email, str) or not email):
        raise ConfigError(
            f"human-roster entry {label}: 'email' must be a non-empty string when present"
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


# --- approver role: OTAMAN_HUMAN -> entry resolution (hitl-default-approver 1.1) ---
#
# The single eligibility primitive that cli-agent's step 2 builds on: BOTH the
# HITL actor path and console spec-approval resolve the same entry and check the
# same role, so "may confirm" and "may approve" cannot drift.


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _identity_candidates(entry: HumanRosterEntry) -> set[str]:
    """The lowercased identities an ``OTAMAN_HUMAN`` value may match this entry by.

    Covers name, a slug of the name, the email, and the email local-part — so a
    roster-id derived by provisioning (from a name or an email-shaped key
    comment) resolves regardless of which form it took.
    """
    cands: set[str] = set()
    if entry.name:
        cands.add(entry.name.strip().lower())
        cands.add(_slug(entry.name))
    if entry.email:
        cands.add(entry.email.strip().lower())
        cands.add(entry.email.split("@", 1)[0].strip().lower())
    return {c for c in cands if c}


def resolve_roster_human(
    roster: list[HumanRosterEntry], otaman_human: str | None
) -> HumanRosterEntry | None:
    """Resolve the roster entry for an ``OTAMAN_HUMAN`` identity, or ``None``.

    Matches case-insensitively against the entry's name / name-slug / email /
    email local-part. Returns the first matching entry; ``None`` when the
    identity is empty or unresolved (callers keep today's unverified behavior on
    ``None`` rather than refusing).
    """
    if not otaman_human or not otaman_human.strip():
        return None
    key = otaman_human.strip().lower()
    for entry in roster:
        if key in _identity_candidates(entry):
            return entry
    return None


def is_approver(entry: HumanRosterEntry) -> bool:
    """True if ``entry`` carries the well-known :data:`APPROVER_ROLE`."""
    return APPROVER_ROLE in entry.roles


def resolve_approver(
    roster: list[HumanRosterEntry], otaman_human: str | None
) -> HumanRosterEntry | None:
    """Resolve the approver entry for an ``OTAMAN_HUMAN`` id.

    Returns the entry only when it resolves AND holds :data:`APPROVER_ROLE`;
    ``None`` otherwise. Callers that must distinguish "unresolved" (unchanged
    behavior) from "resolved but not an approver" (refuse, naming the role)
    should use :func:`resolve_roster_human` + :func:`is_approver` directly.
    """
    entry = resolve_roster_human(roster, otaman_human)
    return entry if entry is not None and is_approver(entry) else None


# --- doctor checks (hitl-default-approver 1.2) ---


@dataclass(frozen=True)
class DoctorFinding:
    """One doctor result. ``level`` is ``"error"`` or ``"warn"``."""

    level: str
    message: str


def check_approver_config(
    roster: list[HumanRosterEntry],
    *,
    hitl_configured: bool = False,
    pending_proposals: bool = False,
) -> list[DoctorFinding]:
    """Doctor checks for the approver grant.

    - ERROR when the approval path is live (``hitl_configured`` OR
      ``pending_proposals``) but no roster entry holds :data:`APPROVER_ROLE` —
      proposals and HITL requests cannot be actioned.
    - WARN for each approver entry missing ``email`` (degrades pm-sync assignee
      resolution + approver notifications).

    Returns findings in order (error first, then per-entry warns); empty when
    healthy. The caller (``otaman doctor``) renders them.
    """
    findings: list[DoctorFinding] = []
    approvers = [e for e in roster if is_approver(e)]

    if (hitl_configured or pending_proposals) and not approvers:
        why = " and ".join(
            r
            for r in (
                "HITL adapters are configured" if hitl_configured else "",
                "pending proposals exist" if pending_proposals else "",
            )
            if r
        )
        findings.append(
            DoctorFinding(
                "error",
                f"{why} but no human-roster entry holds the '{APPROVER_ROLE}' role — "
                "spec-change-requests and HITL confirmations cannot be actioned; add "
                f"roles: [{APPROVER_ROLE}] to a roster entry",
            )
        )

    for entry in approvers:
        if not entry.email:
            findings.append(
                DoctorFinding(
                    "warn",
                    f"human-roster approver entry {entry.name!r} has no email — pm-sync "
                    "assignee resolution and approver notifications are degraded; add an email",
                )
            )

    return findings


__all__ = [
    "APPROVER_ROLE",
    "ConfigError",
    "DoctorFinding",
    "HumanRosterEntry",
    "check_approver_config",
    "is_approver",
    "load_human_roster",
    "parse_human_roster",
    "resolve_approver",
    "resolve_roster_human",
]
