# RoutingDecision Struct — Research (task 1.3)

**Author**: core-agent  
**Date**: 2026-05-27  
**Change**: otaman-router-v1-design  
**Output location**: `otaman-core/src/otaman_core/routing.py` (proposed)

---

## Summary

`RoutingDecision` is the struct returned by the router in response to a `RoutingRequest`.
It tells the bridge exactly which (harness, backend, model) triple to use for the session,
which rule matched, and whether compliance was cleared.

---

## Python Definition

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutingDecision:
    """Routing decision returned by the router for a RoutingRequest.

    Returned on the happy path (200 OK for the HTTP sidecar, or direct return
    for the in-process mode). The bridge uses this to configure the adapter:
    select the harness implementation and pass the backend + model as env vars
    or config values to the harness process.

    Errors (no eligible backend, router unavailable) are communicated via
    dedicated exception types rather than a nullable RoutingDecision, to make
    the error path explicit.

    See: otaman-router-v1-design Q8 (wire protocol).
    """

    # ── What to use ────────────────────────────────────────────────────────

    harness: str
    """Harness identifier (e.g. "claude-code", "openai-agents-sdk").
    Matches the adapter's runtime_id. The bridge uses this to instantiate
    the correct SessionAdapter.
    Required."""

    backend: str
    """Backend identifier (e.g. "anthropic", "azure-openai", "vllm").
    Passed to the harness as its model-provider configuration.
    Required."""

    model: str
    """Specific model to use within the backend
    (e.g. "claude-sonnet-4-6", "gpt-4o", "llama-3.3-70b-instruct").
    The router resolves this from the routing.yaml backend block.
    Required."""

    # ── Why this decision ─────────────────────────────────────────────────

    rule_matched: str
    """Name of the routing rule that produced this decision.
    v1 values: "compliance", "specialisation", "cost", "default".
    Included in the session's audit log for traceability and debugging.
    Required."""

    # ── Cost estimate ─────────────────────────────────────────────────────

    cost_estimate_usd: float | None
    """Router's estimate for this session, in USD. Computed from the LiteLLM
    pricing feed (or static fallback) using the model and an estimated token
    budget derived from typical sessions on this task type.
    None if cost tracking is disabled (routing.yaml cost.source: none).
    Optional."""

    # ── Compliance signal ─────────────────────────────────────────────────

    compliance_cleared: bool
    """True if the selected backend is declared cleared for the
    task_classification in RoutingRequest.
    False is not returned in normal operation (the router raises
    RoutingNoEligibleBackend instead); this field is True on all
    successful routing decisions.
    Included explicitly so the bridge can assert it in tests and so it
    is recorded unambiguously in the audit log.
    Required."""

    # ── Correlation ────────────────────────────────────────────────────────

    routing_id: str
    """Unique identifier for this routing decision (UUID).
    Cross-referenced in the session audit log. Format: "route-<uuid4-short>".
    Required."""

    def __post_init__(self) -> None:
        if not self.harness or not self.harness.strip():
            raise ValueError("RoutingDecision.harness is required")
        if not self.backend or not self.backend.strip():
            raise ValueError("RoutingDecision.backend is required")
        if not self.model or not self.model.strip():
            raise ValueError("RoutingDecision.model is required")
        if self.rule_matched not in {"compliance", "specialisation", "cost", "default"}:
            raise ValueError(
                f"RoutingDecision.rule_matched must be one of "
                "'compliance', 'specialisation', 'cost', 'default'; "
                f"got {self.rule_matched!r}"
            )
        if self.cost_estimate_usd is not None and self.cost_estimate_usd < 0:
            raise ValueError(
                "RoutingDecision.cost_estimate_usd must be non-negative"
            )
        if not self.routing_id or not self.routing_id.strip():
            raise ValueError("RoutingDecision.routing_id is required")
```

**Wire representation** (JSON, per Q8 design.md):

```json
{
  "harness":           "claude-code",
  "backend":           "anthropic",
  "model":             "claude-sonnet-4-6",
  "rule_matched":      "default",
  "cost_estimate_usd": 0.012,
  "compliance_cleared": true,
  "routing_id":        "route-def456"
}
```

---

## Field Rationale

### `harness` + `backend` + `model`

The three-tuple `(harness, backend, model)` is the full routing output per ADR-003:
the two-dimensional adapter contract means routing must specify *both* harness and backend.
`model` is the third dimension resolved from `routing.yaml`'s backend block.

The bridge uses these three fields to:
1. Instantiate the right `SessionAdapter` subclass (by `harness`).
2. Pass `backend` and `model` to the adapter as its configuration (via env vars or the
   adapter's `BackendConfig` — see `spawn.py`'s `BackendConfig`).

### `rule_matched`

Constrained to `{"compliance", "specialisation", "cost", "default"}` — exactly the four
v1 rules. This value appears in the audit JSONL and allows post-hoc analysis:
- What fraction of sessions trigger compliance routing?
- Is the specialisation rule ever used?
- How much of our spend comes from cost-rule sessions (cheapest backend) vs. default?

An enum was considered but `rule_matched: str` is simpler for JSON serialization and
extensible without a code change (future rules don't require an enum update).

**Note**: `rule_matched` validation in `__post_init__` enforces the v1 value set. When
v2 rules are added, this validation expands; no wire-format change needed.

### `compliance_cleared: bool`

Always `True` on a successful `RoutingDecision`. The field exists so:
1. Tests can assert it explicitly.
2. The audit log records the compliance signal unambiguously (no implicit "it must be
   True because we got a 200").
3. A future path where the router returns a "routing with caveat" decision (cleared but
   with a warning annotation) can set this to `True` while adding a separate `caveat`
   field, preserving backward compatibility.

### `cost_estimate_usd: float | None`

Optional because cost tracking is disabled in CE deployments
(`routing.yaml cost.source: none`). The bridge stores this in the audit log when present;
future session-accounting can aggregate it for per-org billing.

### `routing_id`

Format: `"route-<8-char-uuid4-prefix>"` recommended. Example: `"route-def456ab"`.
The bridge logs this in the session's audit JSONL. The router logs it on its side.
Enables end-to-end tracing: `session_id` from `RoutingRequest` + `routing_id` from
`RoutingDecision` together identify the exact route evaluation.

---

## Error Path (not encoded in RoutingDecision)

When no eligible backend exists or the router is unavailable, the router does NOT return
a `RoutingDecision`. It raises (in-process mode) or returns an HTTP error (sidecar mode):

| Condition | In-process exception | HTTP status |
|---|---|---|
| No backend passes rule 1 (compliance) | `RoutingNoEligibleBackend` | `409 Conflict` |
| No backend passes rules 2-3 + default empty | `RoutingNoEligibleBackend` | `409 Conflict` |
| Budget exceeded on all eligible backends (hard mode) | `RoutingBudgetExceeded` | `409 Conflict` |
| Router startup incomplete (secrets not resolved) | `RouterNotReady` | `503 Service Unavailable` |

```python
class RoutingError(RuntimeError):
    """Base for all routing errors. Raised by the router, caught by the bridge."""

class RoutingNoEligibleBackend(RoutingError):
    """No backend is cleared + within budget for this request.

    Attributes:
        rule_blocked:  which rule blocked all backends ("compliance" | "cost" | …)
        constraint:    human-readable description of the violated constraint
    """
    def __init__(self, rule_blocked: str, constraint: str) -> None:
        super().__init__(
            f"No eligible backend: rule={rule_blocked!r}, constraint={constraint!r}"
        )
        self.rule_blocked = rule_blocked
        self.constraint = constraint

class RoutingBudgetExceeded(RoutingNoEligibleBackend):
    """All eligible backends would exceed the cost budget (hard mode only)."""

class RouterNotReady(RoutingError):
    """Router is starting up; secrets or config not yet resolved."""
```

These error types also live in `otaman_core/routing.py`.

---

## Audit Log Integration

After receiving a `RoutingDecision`, the bridge appends an audit event:

```json
{
  "event":             "routing_decision",
  "timestamp":         "2026-05-27T14:00:01Z",
  "session_id":        "sess-abc123",
  "routing_id":        "route-def456ab",
  "org_id":            "org-acme",
  "user_id":           "user-xyz",
  "task_classification": "sensitive",
  "harness":           "claude-code",
  "backend":           "anthropic",
  "model":             "claude-sonnet-4-6",
  "rule_matched":      "default",
  "cost_estimate_usd": 0.012,
  "compliance_cleared": true
}
```

This event is the primary record for compliance auditing (rule 1) and cost tracking.

---

## Open Questions

1. **`rule_matched` for ties**: if rules 2 and 3 both agree on the same backend, which
   rule is recorded? Proposed: the *first* matching rule in the chain (compliance →
   specialisation → cost → default) is the canonical match. If specialisation picks a
   backend that is also the cheapest, `rule_matched = "specialisation"`.
2. **Future `caveat` field**: deferred to v2. A routing-with-caveat result (e.g., "PHI
   cleared, but BAA not yet countersigned — operating under provisional coverage") would
   extend this struct.
3. **`cost_estimate_usd` granularity**: LiteLLM provides per-model input/output token
   pricing; the router's estimate is input_tokens_estimate × price_in + output_tokens_estimate
   × price_out. Token budget estimates for session types (code review = ~10k tokens,
   document summarisation = ~50k tokens) are configuration values in `routing.yaml`.
