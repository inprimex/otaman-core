"""Spec lifecycle enforcement substrate (spec-lifecycle-enforcement 1.1-1.4).

The core keystone of the spec lifecycle: the stage machine, the ``spec_policy``
config layer, the three local gates, and the ratify backend. The repo is the
source of truth — each change's ``.openspec.yaml`` carries ``stage:`` and
``approved_by:`` (D1); bus signals are derived notifications, never consulted as
truth. Everything here is a LOCAL check requiring zero CI or git-host
integration (D2/Q5): a tenant with no integrations keeps a fully working
lifecycle. CI, where configured, is an additional layer.

This module is the substrate ONLY (tasks 1.1-1.4). The surfaces that render it —
``otaman spec status``, the doctor section, the ``otaman ratify`` verb, the CI
gate template — live in otaman-cli (2.x) and otaman-plugin (3.x) and consume the
shapes defined here. Everything is CE-runnable and configurable via
``spec_policy``; EE-only extras (an OPA/Rego engine) are a separate, optional
layer (D8).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from otaman_core.human_roster import HumanRosterEntry

# ---------------------------------------------------------------------------
# 1.1 — Stage model

#: The lifecycle stages, in forward order (D1 / spec delta).
STAGES: tuple[str, ...] = (
    "pre-proposal",
    "proposed",
    "approved",
    "authored",
    "spec-approved",
    "dispatched",
    "implemented",
    "verified",
    "archived",
)

#: Stages exempt from every gate — research produces solutions, dispatches
#: nothing (Q9 sub-ruling). A ``skip_specs``/``research`` flag also exempts.
RESEARCH_STAGES: frozenset[str] = frozenset({"pre-proposal"})

#: The HITL review stage a change must reach before dispatch (D5).
SPEC_APPROVED_STAGE = "spec-approved"

#: The roster hat that mints ``spec-approved`` (D5 / Q8); absent, the default
#: human approver stands in (reuses hitl-default-approver).
CTO_ROLE = "cto"


class SpecLifecycleError(ValueError):
    """Raised on an invalid stage, transition, or ratify call."""


def stage_index(stage: str) -> int:
    """The ordinal of ``stage`` in :data:`STAGES`; raises on an unknown stage."""
    try:
        return STAGES.index(stage)
    except ValueError:
        raise SpecLifecycleError(f"unknown stage: {stage!r}") from None


def is_forward_transition(frm: str, to: str) -> bool:
    """Whether ``to`` is a strictly later stage than ``frm`` (forward progress)."""
    return stage_index(to) > stage_index(frm)


def next_stage(stage: str) -> str | None:
    """The immediately following stage, or ``None`` at ``archived``."""
    i = stage_index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def is_research(change: Mapping[str, Any]) -> bool:
    """Whether a change is research-stage / spec-exempt.

    True when its ``stage`` is in :data:`RESEARCH_STAGES` or it carries a truthy
    ``skip_specs``/``research`` flag. Research changes dispatch nothing and are
    exempt from every gate.
    """
    if change.get("stage") in RESEARCH_STAGES:
        return True
    return bool(change.get("skip_specs") or change.get("research"))


def spec_approved_reached(change: Mapping[str, Any]) -> bool:
    """Whether the change's stage is at or past ``spec-approved`` (D5)."""
    stage = change.get("stage")
    if not isinstance(stage, str) or stage not in STAGES:
        return False
    return stage_index(stage) >= stage_index(SPEC_APPROVED_STAGE)


def has_approval(change: Mapping[str, Any]) -> bool:
    """Whether the change carries a non-empty ``approved_by`` (D1)."""
    approved_by = change.get("approved_by")
    return isinstance(approved_by, str) and bool(approved_by.strip())


def amendment_reenters_review(*, touches_capability_delta: bool, is_research_change: bool) -> bool:
    """Whether an amendment must re-enter ``spec-approved`` review (D5 / Q9).

    Only amendments that touch capability deltas re-enter; design-note-only
    amendments do not, and research-stage changes are always exempt.
    """
    if is_research_change:
        return False
    return touches_capability_delta


# ---------------------------------------------------------------------------
# .openspec.yaml read / write (repo is truth, D1)


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # local import keeps the module yaml-optional at import time

    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def read_openspec(path: Path) -> dict[str, Any]:
    """Read a change's ``.openspec.yaml`` into a mapping (``{}`` if absent/bad)."""
    return _load_yaml(path)


def read_stage(path: Path) -> str | None:
    """The ``stage:`` recorded in a change's ``.openspec.yaml`` (repo is truth)."""
    stage = read_openspec(path).get("stage")
    return stage if isinstance(stage, str) and stage else None


def set_stage(path: Path, stage: str) -> None:
    """Write ``stage:`` into ``.openspec.yaml`` in place, preserving other keys.

    Validates the stage against :data:`STAGES`. Round-trips through PyYAML with
    key order preserved (``sort_keys=False``); atomic replace. Comments are not
    preserved — ``.openspec.yaml`` is machine-owned key/value metadata.
    """
    if stage not in STAGES:
        raise SpecLifecycleError(f"unknown stage: {stage!r}")
    _rewrite_yaml_key(path, "stage", stage)


def _rewrite_yaml_key(path: Path, key: str, value: Any) -> None:
    """Set ``key: value`` in a YAML file, preserving the rest; atomic replace."""
    import os

    import yaml

    data = _load_yaml(path)
    data[key] = value
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# spec-approved approver resolution (D5)


def resolve_spec_approver(
    roster: list[HumanRosterEntry],
    otaman_human: str | None,
) -> HumanRosterEntry | None:
    """Resolve who may mint ``spec-approved`` for an ``OTAMAN_HUMAN`` identity.

    The resolved roster human must hold the :data:`CTO_ROLE` hat OR the
    :data:`~otaman_core.human_roster.APPROVER_ROLE` (the default human approver
    stands in when no cto exists — founder-mode, D5/Q8). Returns the entry, or
    ``None`` when the identity does not resolve to an eligible approver.
    """
    from otaman_core.human_roster import APPROVER_ROLE, resolve_roster_human

    entry = resolve_roster_human(roster, otaman_human)
    if entry is None:
        return None
    if CTO_ROLE in entry.roles or APPROVER_ROLE in entry.roles:
        return entry
    return None


def has_cto(roster: Iterable[HumanRosterEntry]) -> bool:
    """Whether any roster entry holds the :data:`CTO_ROLE` hat."""
    return any(CTO_ROLE in e.roles for e in roster)


# ---------------------------------------------------------------------------
# 1.2 — spec_policy config layer

#: Valid ``spec_policy.enforcement`` modes (D2/Q2). ``self-waive`` is an explicit
#: platform.yaml opt-in, never implicit.
ENFORCEMENT_MODES: tuple[str, ...] = ("block", "warn", "self-waive")
DEFAULT_ENFORCEMENT = "warn"  # tenant default; our program sets ``block``

#: Valid ``spec_policy.process.level`` values, lightest first (D7 / JTBD-109).
PROCESS_LEVELS: tuple[str, ...] = ("spec-first", "solutions", "outcomes", "verified")
DEFAULT_LEVEL = "spec-first"  # L1 — the mandatory spec gate + ONE approver hat

#: Valid ``delivery`` modes (console-lifecycle-actions D3). ``hitl`` needs a human
#: acceptance stop; ``auto`` skips only that stop (auto-archive stays gate-gated).
DELIVERY_MODES: tuple[str, ...] = ("hitl", "auto")
DEFAULT_DELIVERY = "hitl"  # absent delivery = hitl (a change needs acceptance by default)


@dataclass(frozen=True)
class SpecPolicy:
    """A resolved ``spec_policy`` block (D2/D6/D7).

    Defaults are L1 (``spec-first``) with ``warn`` enforcement — the lightest
    conforming policy. ``process_level`` never gets heavier at L1; higher levels
    only ADD requirements (the levels-only-ADD invariant). ``delivery_default``
    is the program-wide fallback delivery mode (console-lifecycle-actions D3).
    """

    mandatory_proposal: bool = True
    approvers: tuple[str, ...] = ("approver",)
    ratifiers: tuple[str, ...] = ("approver",)
    unapproved_stages: tuple[str, ...] = ("pre-proposal",)
    enforcement: str = DEFAULT_ENFORCEMENT
    process_level: str = DEFAULT_LEVEL
    delivery_default: str = DEFAULT_DELIVERY


def _as_str_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return default


def parse_spec_policy(block: Any) -> SpecPolicy:
    """Parse a ``spec_policy:`` mapping into :class:`SpecPolicy`.

    Lenient: an absent block yields L1 defaults; invalid enum values
    (``enforcement``, ``process.level``) fall back to their defaults rather than
    raising (a surface flags them loudly). ``process.level`` reads the nested
    ``process: {level: …}`` shape (D7).
    """
    if not isinstance(block, Mapping):
        return SpecPolicy()

    enforcement = block.get("enforcement")
    if enforcement not in ENFORCEMENT_MODES:
        enforcement = DEFAULT_ENFORCEMENT

    process = block.get("process")
    level = process.get("level") if isinstance(process, Mapping) else None
    if level not in PROCESS_LEVELS:
        level = DEFAULT_LEVEL

    delivery_default = block.get("delivery_default")
    if delivery_default not in DELIVERY_MODES:
        delivery_default = DEFAULT_DELIVERY

    return SpecPolicy(
        mandatory_proposal=bool(block.get("mandatory_proposal", True)),
        approvers=_as_str_tuple(block.get("approvers"), ("approver",)),
        ratifiers=_as_str_tuple(block.get("ratifiers"), ("approver",)),
        unapproved_stages=_as_str_tuple(block.get("unapproved_stages"), ("pre-proposal",)),
        enforcement=enforcement,
        process_level=level,
        delivery_default=delivery_default,
    )


def resolve_spec_policy(
    org_block: Any = None,
    program_block: Any = None,
) -> SpecPolicy:
    """Resolve the effective ``spec_policy`` with org-default / program-override.

    Per-key: the program value wins where present, else the org value, else the
    L1 default (the credential-access cascade precedent, Q5). A program that sets
    nothing inherits the org policy; a program that sets only ``enforcement``
    overrides just that key.
    """
    merged: dict[str, Any] = {}
    for block in (org_block, program_block):
        if isinstance(block, Mapping):
            for k, v in block.items():
                if v is not None:
                    merged[k] = v
    # Nested process.level: program.process overrides org.process per-key too.
    org_proc = org_block.get("process") if isinstance(org_block, Mapping) else None
    prog_proc = program_block.get("process") if isinstance(program_block, Mapping) else None
    proc: dict[str, Any] = {}
    for p in (org_proc, prog_proc):
        if isinstance(p, Mapping):
            proc.update({k: v for k, v in p.items() if v is not None})
    if proc:
        merged["process"] = proc
    return parse_spec_policy(merged)


def solutions_stage_required(
    policy: SpecPolicy,
    *,
    impact_ge_l: bool = False,
    estimate_medium_plus: bool = False,
    multi_repo: bool = False,
) -> bool:
    """Whether the solutions stage is required for a change (R1 scale-adaptive).

    At ``spec-first`` (L1) the solutions stage is NEVER required — L1 never gets
    heavier. At ``solutions`` and above the stage is ADDED, but only when the
    scale triggers fire: impact ≥ L, a Medium+ estimate, or multi-repo scope (D7
    R1). None firing means even a higher-level program skips it for a small
    single-repo change.
    """
    if policy.process_level == "spec-first":
        return False
    return impact_ge_l or estimate_medium_plus or multi_repo


# ---------------------------------------------------------------------------
# 1.3 — the three local gates (D2/D6)

GATES: tuple[str, ...] = ("merge", "dispatch", "archive")


@dataclass(frozen=True)
class GateDecision:
    """The outcome of a lifecycle gate check (values-free, render-agnostic).

    ``allowed`` is whether the operation proceeds. ``waived`` is True when it
    proceeds DESPITE violations (``warn`` or ``self-waive``) — the surface prints
    ``notices`` in that case (self-waive must be visible, never silent). ``mode``
    echoes the enforcement mode that produced the decision.
    """

    gate: str
    allowed: bool
    mode: str
    waived: bool = False
    violations: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


def _decide(gate: str, policy: SpecPolicy, violations: list[str]) -> GateDecision:
    """Apply the enforcement mode to a list of violations."""
    mode = policy.enforcement
    if not violations:
        return GateDecision(gate=gate, allowed=True, mode=mode)
    if mode == "block":
        return GateDecision(gate=gate, allowed=False, mode=mode, violations=tuple(violations))
    # warn | self-waive both proceed, loudly
    prefix = "self-waive" if mode == "self-waive" else "proceeding despite"
    notices = tuple(f"{prefix}: {v}" for v in violations)
    return GateDecision(
        gate=gate,
        allowed=True,
        mode=mode,
        waived=True,
        violations=tuple(violations),
        notices=notices,
    )


def _outcome_violation(
    change: Mapping[str, Any],
    *,
    jtbd_enabled: bool,
    outcome_ok: bool | None,
) -> str | None:
    """The outcome-link violation, if any (D6). Skipped when JTBD is disabled."""
    if not jtbd_enabled:
        return None  # a program without the registries is untaxed
    outcome = change.get("outcome")
    if not (isinstance(outcome, str) and outcome.strip()):
        return "missing outcome link (outcome: <id> required when JTBD support is enabled)"
    if outcome_ok is False:
        return f"outcome {outcome!r} is not Approved with a chosen solution"
    return None


def check_merge_gate(
    change: Mapping[str, Any],
    policy: SpecPolicy,
    *,
    has_capability_delta: bool = True,
    jtbd_enabled: bool = False,
    outcome_ok: bool | None = None,
) -> GateDecision:
    """Merge-path gate: a delta-bearing change needs a valid approval (D2).

    Research-stage and delta-free changes pass unconditionally. Otherwise the
    change must carry ``approved_by`` (D1); when JTBD support is enabled it must
    also carry an Approved ``outcome`` (D6). The enforcement mode decides whether
    a violation refuses, warns, or self-waives.
    """
    if is_research(change) or not has_capability_delta:
        return GateDecision(gate="merge", allowed=True, mode=policy.enforcement)
    violations: list[str] = []
    if not has_approval(change):
        violations.append("no valid approval (approved_by missing)")
    outcome_v = _outcome_violation(change, jtbd_enabled=jtbd_enabled, outcome_ok=outcome_ok)
    if outcome_v:
        violations.append(outcome_v)
    return _decide("merge", policy, violations)


def check_dispatch_gate(
    change: Mapping[str, Any],
    policy: SpecPolicy,
) -> GateDecision:
    """Dispatch-time gate: refuse dispatch before ``spec-approved`` (D5).

    Research-stage changes are exempt (they dispatch nothing). Any other change
    must have reached the ``spec-approved`` stage; absent it, the mode decides.
    """
    if is_research(change):
        return GateDecision(gate="dispatch", allowed=True, mode=policy.enforcement)
    violations: list[str] = []
    if not spec_approved_reached(change):
        violations.append("not spec-approved (authored artifacts lack a HITL approval)")
    return _decide("dispatch", policy, violations)


def check_archive_gate(
    change: Mapping[str, Any],
    policy: SpecPolicy,
    *,
    has_capability_delta: bool = True,
) -> GateDecision:
    """Archive-time gate: a delta-bearing change must have been approved (D2/Q6).

    Prevents archiving an unapproved reversed-lifecycle change. Research-stage
    and delta-free changes pass. Otherwise ``approved_by`` must be present.
    """
    if is_research(change) or not has_capability_delta:
        return GateDecision(gate="archive", allowed=True, mode=policy.enforcement)
    violations: list[str] = []
    if not has_approval(change):
        violations.append("archiving an unapproved change (approved_by missing)")
    return _decide("archive", policy, violations)


# ---------------------------------------------------------------------------
# 1.4 — ratify backend (D4)


@dataclass(frozen=True)
class Ratification:
    """A loud, human-only, audited ratification of pre-approval work (D4).

    Distinct from a propose: the audit trail shows ``ratified: true`` and a
    mandatory ``reason``. ``at`` is a caller-supplied ISO timestamp (injected for
    testing / determinism). Minting one moves the change to ``approved``.
    """

    change: str
    by: str
    reason: str
    at: str
    ratified: bool = True


def ratify(change: str, *, by: str, reason: str, at: str) -> Ratification:
    """Mint a ratification for ``change`` — human-only, mandatory reason (D4).

    ``by`` is the human identity performing the ratification (the HUMAN-DECISION
    tier is enforced by the calling verb, not here); ``reason`` must be non-empty.
    Raises :class:`SpecLifecycleError` otherwise. The returned record's
    ``ratified`` marker keeps it distinct from a propose in the audit trail.
    """
    if not (by and by.strip()):
        raise SpecLifecycleError("ratify is human-only: a 'by' identity is required")
    if not (reason and reason.strip()):
        raise SpecLifecycleError("ratify requires a reason")
    return Ratification(change=change, by=by.strip(), reason=reason.strip(), at=at, ratified=True)


def apply_ratification(change_dict: dict[str, Any], ratification: Ratification) -> dict[str, Any]:
    """Return ``change_dict`` updated with the ratified approval + ``approved`` stage.

    Records ``approved_by`` as a ratified marker, sets ``stage: approved`` and
    ``ratified: true`` — the shape ``set_stage`` / the CI gate read back. Pure:
    returns a new dict, does not mutate the input.
    """
    out = dict(change_dict)
    out["stage"] = "approved"
    out["ratified"] = True
    out["approved_by"] = f"ratified: {ratification.by} — {ratification.reason}"
    return out


def ratifications_in_month(
    ratifications: Iterable[Ratification],
    *,
    year: int,
    month: int,
) -> int:
    """Count ratifications minted in a given month — the doctor health signal (D4).

    Matches on the ``YYYY-MM`` prefix of each record's ISO ``at`` timestamp. A
    rising month-over-month count is a process-health alarm; the doctor SECTION
    that renders it lives in cli, this is the backend count.
    """
    prefix = f"{year:04d}-{month:02d}"
    return sum(1 for r in ratifications if isinstance(r.at, str) and r.at.startswith(prefix))


# ---------------------------------------------------------------------------
# spec-approved transition validation (interactive-human-console 2.2)


@dataclass(frozen=True)
class TransitionValidation:
    """The verdict on a proposed ``authored → spec-approved`` transition.

    ``valid`` is whether the console may mint the stage; ``reasons`` names every
    failure when it may not. Values-free — identities/stages only.
    """

    valid: bool
    reasons: tuple[str, ...] = ()


def validate_spec_approved_transition(
    change: Mapping[str, Any],
    *,
    approver: HumanRosterEntry | None,
) -> TransitionValidation:
    """Validate a proposed ``authored → spec-approved`` transition (IHC 2.2 / D5).

    The console (IHC iteration 2) mints the stage; core validates it here. The
    transition is legitimate only when: the change is currently at ``authored``
    (the sole legal predecessor), an eligible spec-approver resolved (the ``cto``
    hat else the default approver — pass the result of
    :func:`resolve_spec_approver`), and the change is not research (research
    dispatches nothing, so it never enters ``spec-approved``). Whether an
    amendment needs this transition at all is a separate question —
    :func:`amendment_reenters_review` — decided before minting.
    """
    reasons: list[str] = []
    if is_research(change):
        reasons.append("research change never enters spec-approved (it dispatches nothing)")
    stage = change.get("stage")
    if stage != "authored":
        reasons.append(f"spec-approved requires the current stage to be 'authored'; got {stage!r}")
    if approver is None:
        reasons.append("no eligible spec-approver (cto hat or the default human approver required)")
    return TransitionValidation(valid=not reasons, reasons=tuple(reasons))


def apply_spec_approved(
    change_dict: dict[str, Any],
    approver: HumanRosterEntry,
) -> dict[str, Any]:
    """Return ``change_dict`` advanced to ``spec-approved`` with the approver recorded.

    The symmetric writer to :func:`validate_spec_approved_transition`: sets
    ``stage: spec-approved`` and a ``spec_approved_by`` marker naming the human
    who approved the authored artifacts (D5). Pure — returns a new dict, does not
    mutate the input, records the identity only, never artifact content.
    """
    out = dict(change_dict)
    out["stage"] = SPEC_APPROVED_STAGE
    out["spec_approved_by"] = approver.name
    return out


# ---------------------------------------------------------------------------
# delivery mode + gate-gated auto-archive (console-lifecycle-actions 2.1)


def read_delivery(change: Mapping[str, Any]) -> str | None:
    """The explicit ``delivery`` on a change (``hitl``/``auto``), or ``None`` if unset.

    A value outside :data:`DELIVERY_MODES` is treated as unset (``None``) —
    :func:`resolve_delivery` then falls back to the policy default.
    """
    value = change.get("delivery")
    return value if value in DELIVERY_MODES else None


def resolve_delivery(change: Mapping[str, Any], policy: SpecPolicy) -> str:
    """The effective delivery mode: the change's explicit ``delivery`` wins, else
    ``policy.delivery_default``, else :data:`DEFAULT_DELIVERY` (D3).

    Claude session permission modes are NOT a source (D3) — only the durable
    ``.openspec.yaml`` field and the program policy default.
    """
    explicit = read_delivery(change)
    if explicit is not None:
        return explicit
    return policy.delivery_default


@dataclass(frozen=True)
class AutoArchiveDecision:
    """Whether a ``verified`` change may auto-archive itself, and why not (D4)."""

    archive: bool
    reasons: tuple[str, ...] = ()


def auto_archive_decision(
    change: Mapping[str, Any],
    policy: SpecPolicy,
    *,
    archive_gate: GateDecision,
) -> AutoArchiveDecision:
    """Decide the automatic ``verified → archived`` transition (D4).

    Auto-archive runs ONLY when delivery resolves to ``auto``, the change is at
    ``verified``, and the archive gate PASSES CLEANLY — ``allowed`` AND NOT
    ``waived`` (a warn/self-waive is not a pass; auto skips the human-acceptance
    stop, never a gate). Pass the result of :func:`check_archive_gate` as
    ``archive_gate``. Autonomy is gate-gated, and stays visible via the delivery
    badge the surfaces render.
    """
    reasons: list[str] = []
    if resolve_delivery(change, policy) != "auto":
        reasons.append("delivery is not 'auto' (human acceptance required)")
    if change.get("stage") != "verified":
        reasons.append(f"auto-archive only from 'verified'; current stage {change.get('stage')!r}")
    if not archive_gate.allowed:
        reasons.append("archive gate refuses")
    elif archive_gate.waived:
        reasons.append("archive gate only passes under a waiver, not cleanly")
    return AutoArchiveDecision(archive=not reasons, reasons=tuple(reasons))


def apply_auto_archive(change_dict: dict[str, Any]) -> dict[str, Any]:
    """Return ``change_dict`` advanced to ``archived``, recorded like any archive (D4).

    Pure — returns a new dict. Records an ``archived_by`` marker noting the
    gate-passing automatic transition so the audit trail is explicit. The caller
    guards with :func:`auto_archive_decision` first.
    """
    out = dict(change_dict)
    out["stage"] = "archived"
    out["archived_by"] = "auto (delivery: auto, gate-passing)"
    return out


__all__ = [
    "CTO_ROLE",
    "DEFAULT_DELIVERY",
    "DEFAULT_ENFORCEMENT",
    "DEFAULT_LEVEL",
    "DELIVERY_MODES",
    "ENFORCEMENT_MODES",
    "GATES",
    "PROCESS_LEVELS",
    "RESEARCH_STAGES",
    "SPEC_APPROVED_STAGE",
    "STAGES",
    "AutoArchiveDecision",
    "GateDecision",
    "Ratification",
    "SpecLifecycleError",
    "SpecPolicy",
    "TransitionValidation",
    "amendment_reenters_review",
    "apply_auto_archive",
    "apply_ratification",
    "apply_spec_approved",
    "auto_archive_decision",
    "check_archive_gate",
    "check_dispatch_gate",
    "check_merge_gate",
    "has_approval",
    "has_cto",
    "is_forward_transition",
    "is_research",
    "next_stage",
    "parse_spec_policy",
    "ratifications_in_month",
    "ratify",
    "read_delivery",
    "read_openspec",
    "read_stage",
    "resolve_delivery",
    "resolve_spec_approver",
    "resolve_spec_policy",
    "set_stage",
    "solutions_stage_required",
    "spec_approved_reached",
    "stage_index",
    "validate_spec_approved_transition",
]
