# DataClassification Enum — Research (task 1.1)

**Author**: core-agent  
**Date**: 2026-05-27  
**Change**: otaman-router-v1-design  
**Output location**: `otaman-core/src/otaman_core/routing.py` (proposed)

---

## Summary

`DataClassification` is an ordered enum that classifies the *sensitivity of the data* a task
may touch. It is used by the router's compliance rule (rule 1) to select backends that are
cleared to handle that classification level. It is also used in `AdapterCapabilities.compliance`
(task 1.4) to declare which classifications each adapter's default backend is certified for.

---

## Python Definition

```python
from __future__ import annotations
from enum import Enum


class DataClassification(str, Enum):
    """Ordered sensitivity tiers for data handled in a task.

    INTERNAL    — non-public data that carries no special regulatory label.
                  Suitable for any backend the operator has vetted.
    SENSITIVE   — commercially sensitive, contractually restricted, or
                  confidential data not subject to specific regulations
                  (NDAs, proprietary source code, internal financials).
    PII         — Personally Identifiable Information under GDPR/CCPA/etc.
                  (names, email addresses, phone numbers, IP addresses).
                  Requires an adapter whose backend has a DPA in place.
    PHI         — Protected Health Information under HIPAA/HiTrust.
                  Requires a signed BAA with the backend provider.
    REGULATED   — Catch-all for non-PHI regulated data: financial
                  (PCI-DSS scope), legal privileged content, EU sovereign
                  requirements (GDPR Article 9 special categories),
                  defence / government data with export-control constraints.
                  Routing must restrict to backends declared compliant for
                  this tier; in many cases that means on-premises vLLM.
    """

    INTERNAL   = "internal"
    SENSITIVE  = "sensitive"
    PII        = "pii"
    PHI        = "phi"
    REGULATED  = "regulated"
```

### Ordering

The tiers express *increasing* compliance burden, not a linear risk score:

```
INTERNAL < SENSITIVE < PII ≈ PHI < REGULATED
```

`PII` and `PHI` are roughly equivalent in compliance effort (both require DPA/BAA) but
from different regulatory frameworks, so they are distinct values rather than an ordered
chain. `REGULATED` is the most restrictive: it includes sovereign / export-control
constraints that may require on-premises deployment.

**Design note**: the routing engine does not compare classifications numerically. Each
backend in `routing.yaml` declares `allowed_classifications: [internal, sensitive, phi]`
and the router performs a membership check (`task_classification in backend.allowed_classifications`).
An ordered comparison API (`≥ SENSITIVE`) would be unsafe because `PII` and `PHI` are
not directly comparable.

---

## Level Definitions

### `INTERNAL`

Data that belongs to the organisation but carries no regulatory label and is not disclosed
outside contractual relationships.

- Typical content: internal documentation, unreleased source code, system logs,
  infrastructure configuration, sprint planning materials.
- Backend requirement: any backend in the operator's backend pool; no special contract needed.
- Default for sessions where no explicit classification is set.

### `SENSITIVE`

Commercially sensitive data subject to NDA or contractual restrictions; data whose leak
would cause commercial harm without triggering a specific regulatory response.

- Typical content: M&A research, pre-announcement financial data, confidential customer
  lists (without personal details), proprietary algorithms.
- Backend requirement: operator must trust the provider not to train on the data.
  Practically: Claude Code + Anthropic API with data-retention-off policy, or
  OpenAI Agents SDK + Azure OpenAI with DPA, or self-hosted vLLM.
- In `ClaudeCodeAdapter`: Anthropic's API terms (no training on user data with default
  account type); `SENSITIVE` is the highest level cleared by default for this adapter.

### `PII`

Personally Identifiable Information under data-protection laws (GDPR Article 4(1),
CCPA §1798.140(o), UK GDPR, etc.).

- Typical content: names + email addresses, biometric data, national ID numbers, location
  history, IP addresses in combination with identity.
- Backend requirement: signed Data Processing Agreement (DPA) with the backend provider;
  data residency in the correct jurisdiction.
- Cleared adapters (example): `OpenAIAgentsSdkAdapter` + Azure OpenAI (BAA covers DPA
  obligations when configured appropriately); vLLM self-hosted.
- Not cleared by default: plain Anthropic API (no BAA at standard tier).

### `PHI`

Protected Health Information as defined by HIPAA (45 CFR §160.103).

- Typical content: patient medical records, diagnoses, treatment plans, lab results,
  insurance identifiers, any data that identifies a patient in a healthcare context.
- Backend requirement: signed HIPAA Business Associate Agreement (BAA) with the backend
  provider; must comply with HIPAA Security Rule technical safeguards.
- Cleared adapters: `OpenAIAgentsSdkAdapter` + Azure OpenAI (Microsoft BAA available),
  self-hosted vLLM. `ClaudeCodeAdapter` is NOT cleared by default (Anthropic does not
  offer BAA at standard API tier; EE-tier Bedrock-Anthropic can be BAA-covered).
- Note: Otaman EE can configure `ClaudeCodeAdapter` with a Bedrock endpoint that has
  AWS BAA coverage; this is a per-deployment configuration, not a default.

### `REGULATED`

Regulated data not covered by PHI above: financial instruments in PCI-DSS scope, EU
AI Act Annex III high-risk systems, government/defence data with export-control
(ITAR/EAR/Official Sensitive), EU sovereign data requiring Gaia-X / C5-certified cloud.

- Typical content: credit-card numbers in PCI scope, passport/national-ID numbers in
  AML context, classified government documents, dual-use technology specifications.
- Backend requirement: certified / accredited data centre; typically self-hosted vLLM
  on-premises or a sovereign cloud with appropriate certification (BSI C5, FedRAMP
  High, IRAP PROTECTED).
- Cleared adapters: `OpenAIAgentsSdkAdapter` + self-hosted vLLM (operator-deployed) in
  the typical case. Cloud backends may qualify if the operator has appropriate certifications.
- `ClaudeCodeAdapter` is NOT cleared by default for REGULATED data.

---

## Mapping: Task Types → Implied Classification

The bridge (task 2.2) is responsible for mapping a session's context to a
`DataClassification`. The following table provides guidance for v1 rule 1; it is **not**
exhaustive — the bridge may use additional signals (org posture, user role, tool calls).

| Task type / signal | Minimum implied classification | Notes |
|---|---|---|
| Generic code review on internal repo | `INTERNAL` | No customer data in the loop |
| Code touching auth / session tokens | `SENSITIVE` | Credentials in context |
| Code touching customer PII tables | `PII` | DB schema / queries with personal columns |
| Code in a healthcare application reading patient records | `PHI` | HIPAA scope |
| Code in a payment processing service touching card data | `REGULATED` | PCI-DSS scope |
| Agentic task with `Bash` tool invoked on production DB | `SENSITIVE` | Elevated risk, not necessarily regulated |
| Document summarisation: internal meeting notes | `INTERNAL` | — |
| Document summarisation: HR performance reviews | `SENSITIVE` | Personal but not PII in most jurisdictions |
| Document summarisation: user support tickets with emails | `PII` | Email = PII |
| Document summarisation: EHR discharge summaries | `PHI` | — |

---

## Where This Type Lives

`DataClassification` belongs in **`otaman-core`** because:
- It is referenced by `RoutingRequest` (core), `RoutingDecision` (core), and
  `AdapterCapabilities` (core) — all three are core shared types.
- `otaman-router` imports it for rule evaluation.
- `otaman-adapters` imports it for capability declarations.
- `otaman-bridge` imports it to classify tasks before routing.

Proposed module: `otaman_core/routing.py` — new module collecting the routing-related
types (`DataClassification`, `RoutingRequest`, `RoutingDecision`, and `AdapterCapabilities`).
This keeps `spawn.py` focused on session spawning and separates the routing contract.

---

## Open Questions (for design.md / Q2 resolution)

1. **Who sets the classification when the session has no explicit task type?** Bridge
   should default to `INTERNAL` unless the org's `routing.yaml` posture declares
   a stricter default.
2. **Can classification escalate mid-session?** v1: no. Classification is fixed at
   session-start routing time. A future `reclassify` event type could trigger re-routing
   to a different backend mid-session — deferred to v2.
3. **PII vs PHI overlap?** A record that is both PHI and PII (e.g., a patient's email)
   should be classified `PHI` (more specific regulation). Bridge classification logic
   should use the *most restrictive applicable* label.
