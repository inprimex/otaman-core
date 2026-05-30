# RoutingRequest Struct — Research (task 1.2)

**Author**: core-agent  
**Date**: 2026-05-27  
**Change**: otaman-router-v1-design  
**Output location**: `otaman-core/src/otaman_core/routing.py` (proposed)

---

## Summary

`RoutingRequest` is the struct the bridge sends to the router at session-start time.
It carries all signals the router needs to evaluate its four v1 rules:
compliance (rule 1), specialisation (rule 2), cost (rule 3), and default (rule 4).

---

## Python Definition

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence
from uuid import UUID

from .routing import DataClassification   # same module


@dataclass(frozen=True)
class RoutingRequest:
    """Request sent by the bridge to the router at session-start.

    Validated by the router before rule evaluation. The bridge is responsible
    for populating all required fields from its session context and JWT claims.

    See: otaman-router-v1-design Q5 (Zitadel JWT tie-in), Q6 (single-org boundary).
    """

    # ── Identity / authentication ──────────────────────────────────────────

    session_id: str
    """Correlation key for this session; echoed in RoutingDecision.routing_id
    suffix and in the audit log. Format: opaque string (UUID recommended).
    Required."""

    org_id: str
    """Organisation slug whose per-org routing.yaml overlay is applied.
    In Mode 1 (CE, no Zitadel): bridge synthesises org_id from OtamanRoots.org.slug
    (may be the synthesised "default" slug for flat layout).
    Required. Must match the slug grammar: ^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$
    OR the reserved sentinel "_platform"."""

    user_id: str | None
    """Authenticated user identifier (Zitadel sub claim in Mode 2+; None in Mode 1).
    The router uses this for per-user budget tracking (future v2); in v1 it is
    included for audit trail purposes only.
    Optional."""

    roles: tuple[str, ...]
    """Roles asserted for this user in the JWT (Zitadel project roles).
    Empty tuple in Mode 1. The router's compliance rule (rule 1) may use roles
    to restrict backend access (e.g., only users with role 'analyst' can access
    a PHI-cleared backend). In v1 this is informational; the per-org routing.yaml
    overlay encodes the role-to-backend mapping.
    Optional (defaults to empty tuple)."""

    # ── Task classification ────────────────────────────────────────────────

    task_classification: DataClassification
    """Sensitivity of the data this session will handle, as assessed by the bridge.
    The bridge derives this from: org's declared compliance posture, task type,
    tool calls requested, and user role (see bridge task 2.2).
    Required."""

    task_type: str
    """Free-form label for the kind of task (e.g. "code_review", "summarise",
    "code_generation", "security_audit"). Used by rule 2 (specialisation) to
    prefer adapters with declared expertise for this task type.
    Required. No validation on value; unknown values fall through to default rule."""

    # ── Cost signals ───────────────────────────────────────────────────────

    cost_budget_remaining_usd: float | None
    """Remaining cost budget for this org/user, in USD. Sourced from the bridge's
    session-accounting layer (future; bridge task 2.3 will confirm availability).
    None means "no budget constraint" — rule 3 still routes to the cheapest
    eligible backend but does not enforce a ceiling.
    Optional (None = unbounded)."""

    # ── Harness preference ─────────────────────────────────────────────────

    preferred_harness: str | None
    """Optional hint: the caller prefers this harness (e.g. "claude-code").
    The router treats this as a tie-breaking hint after rules 1–3 are evaluated.
    If the preferred harness is not compliant with rules 1–3, it is ignored and
    the compliant result is returned.
    Optional (None = no preference; router picks the best eligible harness)."""

    # ── Timestamp ─────────────────────────────────────────────────────────

    timestamp: datetime
    """Wall-clock time of the routing request (UTC). Used for audit trail and
    for budget-window calculations (e.g., daily spend caps in future v2).
    Required. Must be timezone-aware (UTC)."""

    # ── Defaults ─────────────────────────────────────────────────────────

    @classmethod
    def _default_roles(cls) -> tuple[str, ...]:
        return ()

    def __post_init__(self) -> None:
        if not self.session_id or not self.session_id.strip():
            raise ValueError("RoutingRequest.session_id is required")
        if not self.org_id or not self.org_id.strip():
            raise ValueError("RoutingRequest.org_id is required")
        if not self.task_type or not self.task_type.strip():
            raise ValueError("RoutingRequest.task_type is required")
        if not isinstance(self.task_classification, DataClassification):
            raise TypeError(
                "RoutingRequest.task_classification must be a DataClassification"
            )
        if self.cost_budget_remaining_usd is not None and self.cost_budget_remaining_usd < 0:
            raise ValueError(
                "RoutingRequest.cost_budget_remaining_usd must be non-negative"
            )
        if self.timestamp.tzinfo is None:
            raise ValueError(
                "RoutingRequest.timestamp must be timezone-aware (use UTC)"
            )
```

**Wire representation** (JSON, per Q8 design.md):

```json
{
  "session_id": "sess-abc123",
  "org_id":     "org-acme",
  "user_id":    "user-xyz",
  "roles":      ["developer"],
  "task_classification": "sensitive",
  "task_type":  "code_review",
  "cost_budget_remaining_usd": 0.50,
  "preferred_harness": null,
  "timestamp":  "2026-05-27T14:00:00Z"
}
```

---

## Field Rationale

### Required Fields

| Field | Why required |
|---|---|
| `session_id` | Primary correlation key; ties routing decision to audit log entry |
| `org_id` | Selects the per-org routing.yaml overlay; non-negotiable for multi-tenant |
| `task_classification` | Drives rule 1 (compliance) — the most safety-critical rule |
| `task_type` | Drives rule 2 (specialisation) — needed even if no specialised adapter exists |
| `timestamp` | Audit trail; future budget-window calculations |

### Optional Fields

| Field | Why optional | Behaviour when absent |
|---|---|---|
| `user_id` | Mode 1 (CE, solo) has no authenticated users | Audit trail records "local"; no per-user budget enforced |
| `roles` | Mode 1 has no JWT; empty = no role-based restrictions | Compliance rule uses org posture only |
| `cost_budget_remaining_usd` | Budget tracking is not wired in all deployments | Rule 3 still routes cheapest but doesn't enforce a ceiling |
| `preferred_harness` | Caller may have no preference | Router picks based on rules 1–3 alone |

### `roles: tuple[str, ...]` vs `list[str]`

`tuple` is used because `RoutingRequest` is `frozen=True`. The wire format uses a JSON
array and is deserialized to a `tuple` by the bridge's request builder.

### `task_type: str` (free-form)

Free-form rather than an enum because:
- New task types emerge organically as the platform grows.
- The specialisation rule (rule 2) uses a routing.yaml `specialisation:` block to map
  task types to preferred adapters — no code change needed for new task types.
- Regex or prefix-matching in the rule engine is more flexible than enum membership.

Unknown task types fall through to rule 4 (default routing) without error.

---

## Validation Rules

| Rule | Enforcement |
|---|---|
| `session_id` non-empty | `__post_init__` raises `ValueError` |
| `org_id` non-empty | `__post_init__` raises `ValueError` |
| `task_classification` is a `DataClassification` | `__post_init__` raises `TypeError` |
| `cost_budget_remaining_usd >= 0` when present | `__post_init__` raises `ValueError` |
| `timestamp` is timezone-aware | `__post_init__` raises `ValueError` |
| `task_type` non-empty | `__post_init__` raises `ValueError` |
| `org_id` slug grammar | **Not** validated in `RoutingRequest` — the bridge validates it before constructing the request; router trusts the bridge as the org-context authority |

---

## Relationship to Other Types

```
RoutingRequest ──uses──► DataClassification   (task 1.1)
RoutingRequest ──returns──► RoutingDecision   (task 1.3)
RoutingRequest ──checks──► AdapterCapabilities.compliance  (task 1.4)
```

---

## Open Questions

1. **Budget source at bridge side**: bridge task 2.3 confirms whether `user_id`/`org_id`
   maps to a per-session budget from the session-accounting layer. If not available in
   v1, `cost_budget_remaining_usd` will always be `None`.
2. **`roles` encoding**: Zitadel project roles are strings; the bridge should deduplicate
   and sort them before including in the request (deterministic for caching).
3. **Schema version**: the wire JSON does not include `schema_version` — the `/route`
   endpoint URL carries the version (`/v1/route`).
