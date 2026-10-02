"""The review policy — what a human MUST see in the Messages tree (cmt 1.1).

Roman's console had ~1000 pending messages with the handful that need a decision
buried in task-complete noise. The review policy is the per-program declaration of
which message classes a human must see, grouped so the surface can render them
policy-ordered. This module is the single home for the policy model + its shipped
default, consumed by the cli Messages tree (cmt 1.2): mandatory classes render first
and are never collapsed-hidden, opt-in classes group normally, auto-triage classes
collapse to a count at the bottom.

Three classes (:data:`CLASSES`):

- ``mandatory`` — must be seen; rendered first, never collapse-hidden even when it
  arrives into a collapsed group.
- ``opt-in`` — grouped normally; the DEFAULT for any type the policy does not name.
- ``auto-triage`` — the noise pile (task-complete, status broadcasts) — one collapsed
  count at the bottom.

The shipped default (:func:`default_policy`) is Roman's floor: decision points and
approvals a human owns are mandatory; routine completions and status broadcasts are
auto-triage; everything else is opt-in until a program declares otherwise. A program
overrides per class (:func:`parse_review_policy`); :func:`classify` resolves one
message, with ``priority: urgent`` always mandatory regardless of type. The resolved
policy names its ``source`` so the surface can show whose policy is active.

Pure: config in, classification out. No store access, no rendering (that is cli 1.2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MANDATORY = "mandatory"
OPT_IN = "opt-in"
AUTO_TRIAGE = "auto-triage"
#: The three review classes, in render priority order.
CLASSES: tuple[str, ...] = (MANDATORY, OPT_IN, AUTO_TRIAGE)

#: Shipped-default mandatory types (Roman's floor): a human owns these decisions.
_DEFAULT_MANDATORY: frozenset[str] = frozenset(
    {
        "decision-required",  # an agent is blocked on a human decision
        "spec-change-request",  # awaiting spec approval
        "spec-approval-pending",  # the targeted approval-queue item
        "request-human-review",  # explicit human-review ask
        "outcome-proposal",  # business sign-off
    }
)

#: Shipped-default auto-triage types: routine completions + status broadcasts — the
#: pile that should collapse to a count, not bury the decisions.
_DEFAULT_AUTO_TRIAGE: frozenset[str] = frozenset(
    {
        "task-complete",
        "info",
        "announce",
        "outcome-status-changed",
        "solution-status-changed",
        "lifecycle-change",
        "agent-registry-change",
        "post-commit-review",
    }
)


class ReviewPolicyError(ValueError):
    """A malformed ``review-policy`` declaration."""


@dataclass(frozen=True)
class ReviewPolicy:
    """A resolved review policy: the type sets per class + where it came from.

    ``source`` is ``"shipped-default"`` or ``"program"`` so the surface can name the
    active policy (the undeclared-policy default must name itself). A type absent from
    all three sets classifies as :data:`OPT_IN`.
    """

    mandatory: frozenset[str]
    auto_triage: frozenset[str]
    source: str = "shipped-default"


def default_policy() -> ReviewPolicy:
    """Roman's floor — the policy in force until a program declares its own."""
    return ReviewPolicy(
        mandatory=_DEFAULT_MANDATORY, auto_triage=_DEFAULT_AUTO_TRIAGE, source="shipped-default"
    )


def _str_set(value: Any, where: str) -> frozenset[str]:
    if not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
        raise ReviewPolicyError(f"{where} must be a list of message-type strings")
    return frozenset(value)


def parse_review_policy(block: Any) -> ReviewPolicy:
    """Parse a program's ``review-policy`` block, else the shipped default.

    ``None``/absent -> :func:`default_policy`. A declared block may set ``mandatory``
    and/or ``auto-triage`` type lists (``opt-in`` is implicit — any type named in
    neither); a program-declared policy carries ``source="program"`` so the surface
    names it. A type may not appear in BOTH mandatory and auto-triage — a type cannot
    be must-see and collapsible at once (refused loudly, never silently reconciled).
    """
    if block is None:
        return default_policy()
    if not isinstance(block, Mapping):
        raise ReviewPolicyError("review-policy must be a mapping")
    stray = set(block) - {MANDATORY, AUTO_TRIAGE, OPT_IN}
    if stray:
        raise ReviewPolicyError(f"review-policy: unknown key(s) {sorted(stray)!r}")
    mandatory = _str_set(block.get(MANDATORY, []), "review-policy.mandatory")
    auto_triage = _str_set(block.get(AUTO_TRIAGE, []), "review-policy.auto-triage")
    # opt-in is accepted (explicitness) but needs no stored set — it is the default.
    if OPT_IN in block:
        _str_set(block[OPT_IN], "review-policy.opt-in")
    conflict = mandatory & auto_triage
    if conflict:
        raise ReviewPolicyError(
            f"review-policy: {sorted(conflict)!r} are in both mandatory and auto-triage — "
            "a type cannot be must-see and collapsible at once"
        )
    return ReviewPolicy(mandatory=mandatory, auto_triage=auto_triage, source="program")


def classify(policy: ReviewPolicy, message_type: str, *, priority: str | None = None) -> str:
    """The review class for a message of *message_type* at *priority*.

    ``priority: urgent`` is always :data:`MANDATORY`, whatever the type — an urgent
    item is never collapsed. Otherwise: a type in ``policy.mandatory`` is mandatory, a
    type in ``policy.auto_triage`` is auto-triage, and anything else is :data:`OPT_IN`
    (the default). Mandatory wins, so a type that is somehow in both treats as
    mandatory — must-see is the safe direction.
    """
    if priority == "urgent":
        return MANDATORY
    if message_type in policy.mandatory:
        return MANDATORY
    if message_type in policy.auto_triage:
        return AUTO_TRIAGE
    return OPT_IN


__all__ = [
    "AUTO_TRIAGE",
    "CLASSES",
    "MANDATORY",
    "OPT_IN",
    "ReviewPolicy",
    "ReviewPolicyError",
    "classify",
    "default_policy",
    "parse_review_policy",
]
