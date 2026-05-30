# AdapterCapabilities — Definition + Compliance Extension (task 1.4)

**Author**: core-agent  
**Date**: 2026-05-27  
**Change**: otaman-router-v1-design  
**Output location**: `otaman-core/src/otaman_core/routing.py` (proposed)

---

## Summary

`AdapterCapabilities` is a new type defined per ADR-003 follow-up
("Define `AdapterCapabilities` declaration type"). It describes what a harness adapter
can do — both functional capabilities and compliance clearances.

This research document defines the full `AdapterCapabilities` type and adds the
`compliance: list[DataClassification]` field needed by the router's compliance rule (rule 1).

---

## Current State

`otaman-adapters` currently exposes a `SkillAdapter` Protocol (in `adapter.py`) for
skill registration only — it has `register()` and `unregister()` methods. This is a
*skill-installation* concern, not a *session-running* concern.

ADR-003 (§Consequences) defines a separate, more comprehensive contract for session-running:
- "Define `Adapter` Protocol in `otaman-core` with `run_session(request) -> AsyncIterator[SessionEvent]` shape"
- "Define `AdapterCapabilities` declaration type"

Neither exists yet in code. `AdapterCapabilities` should live in `otaman-core` so that the
router can import it without depending on `otaman-adapters`.

---

## Python Definition

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .routing import DataClassification   # same module


@dataclass(frozen=True)
class AdapterCapabilities:
    """Static capability declaration for a session-running adapter (harness).

    Each adapter implementation declares its capabilities once (typically as a
    class-level constant). The router reads these capabilities to:
    1. Evaluate compliance rule (rule 1): check that compliance_clearances includes
       the session's task_classification.
    2. Evaluate specialisation rule (rule 2): prefer adapters whose
       specialised_task_types matches the requested task type.
    3. Build the routing.yaml capability matrix for operator documentation.

    See: ADR-003 §Capability matrix; otaman-router-v1-design Q5.
    """

    # ── Identity ──────────────────────────────────────────────────────────

    runtime_id: str
    """Harness identifier. Must match the adapter's runtime_id attribute
    and the value stored in RoutingDecision.harness.
    Required."""

    # ── Compliance clearances ─────────────────────────────────────────────

    compliance_clearances: tuple[DataClassification, ...]
    """Data classifications this adapter's default backend configuration is
    cleared to handle.

    "Default" means: as-configured in a standard Otaman deployment without
    per-customer BAA overrides or custom backend wiring.

    The operator can extend clearances via per-org routing.yaml overlays
    (e.g., ClaudeCodeAdapter on Bedrock with AWS BAA may be PHI-cleared),
    but the adapter's DECLARED default is used when no overlay is present.

    See data-classification-levels.md for per-level backend requirements.
    Required. Must not be empty (all adapters at minimum handle INTERNAL).
    """

    # ── Functional capabilities ───────────────────────────────────────────

    supports_pre_tool_hook: bool = True
    """True if the harness supports a pre-tool-call policy hook
    (e.g. Claude Code's PreToolUse hook).
    If False, the bridge must implement its own loop-interception for approvals.
    Per ADR-003 capability matrix."""

    supports_mcp_tools: bool = True
    """True if the harness supports Model Context Protocol (MCP) tools natively.
    All v1 harnesses support MCP; this field guards future v3+ harnesses."""

    supports_plan_mode: bool = False
    """True if the harness supports a plan/preview mode (show plan before executing).
    Claude Code: True (native plan mode). OpenAI Agents SDK: False in v1."""

    supports_subagent_delegation: bool = False
    """True if the harness supports sub-agent / Task tool delegation natively.
    Claude Code: True. OpenAI Agents SDK: False in v1 (deferred)."""

    supports_streaming_transcript: bool = True
    """True if the harness produces a streaming transcript that the bridge
    captures in real time. All v1 harnesses: True (JSONL / event stream)."""

    cost_visibility: str = "none"
    """Level of per-call cost visibility the harness exposes.
    Values: "none" | "limited" | "full".
    "none":    harness provides no cost data; router cost rule uses LiteLLM pricing only.
    "limited": harness provides aggregate session cost after completion (Claude Code).
    "full":    harness provides per-call token counts + costs (OpenAI Agents SDK).
    """

    eligible_backends: tuple[str, ...]
    """Backend identifiers this harness can target. Used by the router to
    validate routing.yaml backend declarations — a rule cannot route to a
    backend the harness cannot talk to.
    Examples: ("anthropic", "bedrock-anthropic", "vertex-anthropic") for
    ClaudeCodeAdapter; ("azure-openai", "openai", "vllm", "litellm") for
    OpenAIAgentsSdkAdapter.
    Required."""

    specialised_task_types: tuple[str, ...]
    """Task types this adapter is specialised for (prefers over a generic adapter).
    Used by rule 2 (specialisation): if the RoutingRequest.task_type matches an
    entry here and no compliance rule wins first, this adapter is preferred.
    Example: Claude Code → ("security_audit", "code_review", "refactor")
    because Claude models have strong code capabilities.
    Empty tuple = no specialisation (generic adapter; falls through to cost/default).
    Optional (defaults to empty)."""

    def __post_init__(self) -> None:
        if not self.runtime_id or not self.runtime_id.strip():
            raise ValueError("AdapterCapabilities.runtime_id is required")
        if not self.compliance_clearances:
            raise ValueError(
                "AdapterCapabilities.compliance_clearances must not be empty; "
                "all adapters at minimum handle DataClassification.INTERNAL"
            )
        if self.cost_visibility not in {"none", "limited", "full"}:
            raise ValueError(
                f"AdapterCapabilities.cost_visibility must be one of "
                "'none', 'limited', 'full'; got {self.cost_visibility!r}"
            )
        if not self.eligible_backends:
            raise ValueError(
                "AdapterCapabilities.eligible_backends must not be empty"
            )
```

---

## Default Capability Declarations (proposed)

These are class-level constants to be defined in `otaman-adapters`
(task for adapters-agent, task 4.1):

### `ClaudeCodeAdapter`

```python
# In otaman-adapters/src/otaman_adapters/claude_code.py

CLAUDE_CODE_CAPABILITIES = AdapterCapabilities(
    runtime_id="claude-code",

    # Cleared by default for INTERNAL and SENSITIVE.
    # PHI: NOT cleared by default (no BAA at standard Anthropic API tier).
    # PHI clearance is possible with Bedrock + AWS BAA — this is a per-deployment
    # override in the per-org routing.yaml overlay, not a default.
    # PII: NOT cleared by default (Anthropic API data-retention controls apply;
    # DPA not provided at standard API tier). Operators using Anthropic API for PII
    # should configure a custom DPA and override via per-org overlay.
    compliance_clearances=(
        DataClassification.INTERNAL,
        DataClassification.SENSITIVE,
    ),

    supports_pre_tool_hook=True,      # native PreToolUse hook
    supports_mcp_tools=True,          # native MCP client
    supports_plan_mode=True,          # native plan mode
    supports_subagent_delegation=True, # native Task tool
    supports_streaming_transcript=True,
    cost_visibility="limited",         # aggregate cost after session; no per-call

    eligible_backends=(
        "anthropic",
        "bedrock-anthropic",
        "vertex-anthropic",
    ),

    specialised_task_types=(
        "code_review",
        "code_generation",
        "refactor",
        "security_audit",
        "test_generation",
    ),
)
```

### `OpenAIAgentsSdkAdapter`

```python
# In otaman-adapters/src/otaman_adapters/openai_agents.py

OPENAI_AGENTS_CAPABILITIES = AdapterCapabilities(
    runtime_id="openai-agents",

    # Cleared by default for INTERNAL, SENSITIVE, PII, PHI, and REGULATED
    # because Azure OpenAI with BAA + HIPAA coverage is the canonical deployment
    # target for this adapter's regulated-customer use case.
    #
    # IMPORTANT: these clearances assume the operator has:
    # - Azure OpenAI endpoint with DPA (for PII)
    # - Signed Microsoft BAA (for PHI)
    # - Appropriate Azure region + data-residency config (for REGULATED)
    #
    # For non-Azure deployments (OpenAI direct), the operator should override
    # compliance_clearances in per-org routing.yaml to [internal, sensitive] only.
    compliance_clearances=(
        DataClassification.INTERNAL,
        DataClassification.SENSITIVE,
        DataClassification.PII,
        DataClassification.PHI,
        DataClassification.REGULATED,
    ),

    supports_pre_tool_hook=False,      # loop-intercept, not native hook
    supports_mcp_tools=True,           # MCP-first architecture
    supports_plan_mode=False,          # not implemented in v1
    supports_subagent_delegation=False, # deferred to v2
    supports_streaming_transcript=True,
    cost_visibility="full",             # per-call token + cost visibility

    eligible_backends=(
        "azure-openai",
        "openai",
        "vllm",
        "litellm",
    ),

    specialised_task_types=(
        "document_analysis",
        "summarise",
        "data_extraction",
    ),
)
```

---

## How the Router Uses `AdapterCapabilities`

### Rule 1 — Compliance

```python
def evaluate_compliance(
    request: RoutingRequest,
    adapters: list[AdapterCapabilities],
) -> RoutingDecision | None:
    """Return the first adapter cleared for request.task_classification."""
    for adapter in adapters:
        if request.task_classification in adapter.compliance_clearances:
            # Also check per-org overlay for backend restriction
            ...
            return RoutingDecision(harness=adapter.runtime_id, ...)
    return None  # pass to next rule
```

### Rule 2 — Specialisation

```python
def evaluate_specialisation(
    request: RoutingRequest,
    adapters: list[AdapterCapabilities],
) -> RoutingDecision | None:
    """Return the first adapter specialised for request.task_type, if compliance allows."""
    for adapter in adapters:
        if (
            request.task_type in adapter.specialised_task_types
            and request.task_classification in adapter.compliance_clearances
        ):
            return RoutingDecision(harness=adapter.runtime_id, rule_matched="specialisation", ...)
    return None
```

---

## Relationship to `SkillAdapter`

`AdapterCapabilities` and `SkillAdapter` are **separate concerns**:

| Type | Concern | Lives in |
|---|---|---|
| `SkillAdapter` | Registering SKILL.md skills into a harness's plugin directory | `otaman-adapters` |
| `AdapterCapabilities` | Declaring what a session-running harness can do (compliance, features) | `otaman-core` |

The `SkillAdapter` Protocol (skill registration) is unaffected by this change.
A separate `SessionAdapter` Protocol (session running, per ADR-003 follow-up) will be
defined in `otaman-core` and will reference `AdapterCapabilities`.

---

## Module Structure (proposed)

```
otaman-core/src/otaman_core/routing.py
├── DataClassification (enum)          ← task 1.1
├── RoutingRequest (dataclass)         ← task 1.2
├── RoutingDecision (dataclass)        ← task 1.3
├── AdapterCapabilities (dataclass)    ← task 1.4 (this document)
├── RoutingError (base exception)      ← defined in task 1.3 doc
├── RoutingNoEligibleBackend           ← defined in task 1.3 doc
├── RoutingBudgetExceeded              ← defined in task 1.3 doc
└── RouterNotReady                     ← defined in task 1.3 doc
```

All four types live in one module to avoid circular imports and make the contract easy
to import:
```python
from otaman_core.routing import (
    DataClassification,
    RoutingRequest,
    RoutingDecision,
    AdapterCapabilities,
    RoutingNoEligibleBackend,
)
```

---

## Backward Compatibility

`AdapterCapabilities` is a **new type** — it does not exist yet. No backward-compatibility
concern for the type definition itself.

The existing `SkillAdapter` Protocol and `ClaudeCodeAdapter`/`OpenAIAgentsAdapter` skill
registration classes are **unaffected** — this change adds new types, not modifies
existing ones.

When the `SessionAdapter` Protocol is added (ADR-003 follow-up implementation task), the
adapters will need to declare `CAPABILITIES: AdapterCapabilities` at class level. This is
additive and backward-compatible with existing adapter tests.

---

## Contract Change

Adding `AdapterCapabilities` is a **new contract** in `otaman-core` — no existing type
is modified. A `contract-change` broadcast is NOT required for adding a new type; it would
be required when the `SessionAdapter` Protocol references it (implementation task).

The `compliance_clearances` values for both v1 adapters (documented above) are the
canonical reference for `adapters-agent` task 4.1.
