"""Stage-1 deterministic proposal gate + critic cost telemetry (JTBD-57 1.1/1.4).

The constitutional gate's *deterministic* half (Hook A, D5/D6): a free, always-on
lint that scores a spec-change-request 0-100 and returns findings — front-matter
schema, resolvable JTBD ids / citations, a secret scan on the body
(gitleaks-lite), no TBD/TODO in critical fields, and ``affected_repos`` resolving
against ``platform.yaml``. It runs synchronously at propose-time (failures return
to the proposer immediately) and on spec-change-request messages.

Comment-never-block (D1): this module only produces a score + findings; it never
refuses a proposal. Stage 2 (the fresh-session Constitutional critic) is a plugin
skill (1.2) and the console/status surfaces are cli (1.3) — they consume the
:class:`LintResult` shape defined here. :func:`record_critic_cost` (1.4) is the
per-critic-invocation cost telemetry that rides the platform usage plumbing (D6).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 1.1 — deterministic lint + 0-100 score


@dataclass(frozen=True)
class LintFinding:
    """One deterministic-lint finding. ``level`` is ``"error"`` or ``"warn"``."""

    code: str
    level: str
    message: str
    field: str | None = None


#: Score tiers, highest first — the five tiers reviewers triage by (D5).
SCORE_TIERS: tuple[tuple[int, str], ...] = (
    (90, "excellent"),
    (75, "strong"),
    (50, "adequate"),
    (25, "weak"),
    (0, "failing"),
)

#: Front-matter fields a proposal must carry (the rubric's structural minimum).
DEFAULT_REQUIRED_FIELDS: tuple[str, ...] = ("title", "outcome", "affected_repos", "artifacts")

#: Fields scanned for placeholder text (TBD/TODO/FIXME/???).
CRITICAL_FIELDS: tuple[str, ...] = ("title", "outcome", "affected_repos", "artifacts")

#: Per-finding score deductions, by level.
_DEDUCTION = {"error": 25, "warn": 10}

#: Placeholder tokens forbidden in critical fields.
_PLACEHOLDER = re.compile(r"\b(TBD|TODO|FIXME|XXX)\b|\?\?\?", re.IGNORECASE)

#: An outcome/JTBD id shape, e.g. ``JTBD-57`` or ``JTBD-99-102``.
_OUTCOME_ID = re.compile(r"^[A-Za-z]+-\d+(?:-\d+)?$")

#: gitleaks-lite: high-confidence secret patterns scanned on the body. Deliberately
#: conservative — the goal is catching a pasted credential, not full DLP.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "generic-secret-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"
        ),
    ),
)


def score_tier(score: int) -> str:
    """The tier label for a 0-100 ``score`` (D5)."""
    for threshold, label in SCORE_TIERS:
        if score >= threshold:
            return label
    return SCORE_TIERS[-1][1]


@dataclass(frozen=True)
class LintResult:
    """The Stage-1 output: a 0-100 ``score``, its ``tier``, and the ``findings``."""

    score: int
    tier: str
    findings: tuple[LintFinding, ...]


def _field_text(value: Any) -> str:
    """Flatten a field value to searchable text (lists joined)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return "" if value is None else str(value)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def scan_secrets(text: str) -> list[str]:
    """Return the codes of secret patterns found in ``text`` (gitleaks-lite).

    Values-free: returns the pattern CODE that matched, never the matched secret.
    """
    hits: list[str] = []
    for code, pattern in _SECRET_PATTERNS:
        if pattern.search(text or ""):
            hits.append(code)
    return hits


def lint_proposal(
    proposal: Mapping[str, Any],
    *,
    platform_repos: Iterable[str],
    required_fields: Sequence[str] = DEFAULT_REQUIRED_FIELDS,
    known_outcomes: Iterable[str] | None = None,
    known_change_ids: Iterable[str] | None = None,
) -> LintResult:
    """Deterministically lint a proposal and emit a 0-100 score + findings (1.1).

    Checks, in order:

    1. **front-matter schema** — every ``required_fields`` key present and
       non-empty (error per missing field);
    2. **resolvable outcome / citations** — ``outcome`` matches the id shape and,
       when ``known_outcomes`` is given, resolves against it; a cited
       ``change_id``, when present and ``known_change_ids`` is given, must
       resolve;
    3. **secret scan** — the ``body`` is scanned by :func:`scan_secrets` (error
       per pattern hit — the CODE only, never the secret);
    4. **no placeholders** — TBD/TODO/FIXME/??? in any :data:`CRITICAL_FIELDS`
       value (error per field);
    5. **affected_repos** — each declared repo resolves against
       ``platform_repos`` (error per unknown repo).

    The score starts at 100 and deducts :data:`_DEDUCTION` per finding, clamped to
    ``[0, 100]``. Never raises and never blocks — the result is advisory (D1).
    """
    findings: list[LintFinding] = []
    repos = set(platform_repos)
    outcomes = set(known_outcomes) if known_outcomes is not None else None
    change_ids = set(known_change_ids) if known_change_ids is not None else None

    # 1. front-matter schema
    for f in required_fields:
        if _is_empty(proposal.get(f)):
            findings.append(
                LintFinding(
                    "missing-field", "error", f"required field {f!r} is missing or empty", f
                )
            )

    # 2. resolvable outcome + citations
    outcome = proposal.get("outcome")
    if isinstance(outcome, str) and outcome.strip():
        if not _OUTCOME_ID.match(outcome.strip()):
            findings.append(
                LintFinding(
                    "malformed-outcome",
                    "warn",
                    f"outcome {outcome!r} is not a resolvable id (expected e.g. JTBD-57)",
                    "outcome",
                )
            )
        elif outcomes is not None and outcome.strip() not in outcomes:
            findings.append(
                LintFinding(
                    "unresolved-outcome",
                    "error",
                    f"outcome {outcome!r} resolves to no registry entry",
                    "outcome",
                )
            )
    change_id = proposal.get("change_id")
    if isinstance(change_id, str) and change_id.strip() and change_ids is not None:
        if change_id.strip() not in change_ids:
            findings.append(
                LintFinding(
                    "unresolved-citation",
                    "error",
                    f"cited change id {change_id!r} does not exist",
                    "change_id",
                )
            )

    # 3. secret scan on body
    for code in scan_secrets(_field_text(proposal.get("body"))):
        findings.append(
            LintFinding("secret-in-body", "error", f"possible secret in body ({code})", "body")
        )

    # 4. no placeholders in critical fields
    for f in CRITICAL_FIELDS:
        if _PLACEHOLDER.search(_field_text(proposal.get(f))):
            findings.append(
                LintFinding("placeholder-in-field", "error", f"placeholder (TBD/TODO) in {f!r}", f)
            )

    # 5. affected_repos resolve against platform.yaml
    affected = proposal.get("affected_repos")
    if isinstance(affected, (list, tuple)):
        for repo in affected:
            if isinstance(repo, str) and repo not in repos:
                findings.append(
                    LintFinding(
                        "unknown-repo",
                        "error",
                        f"affected repo {repo!r} is not declared in platform.yaml",
                        "affected_repos",
                    )
                )

    score = 100 - sum(_DEDUCTION.get(f.level, 0) for f in findings)
    score = max(0, min(100, score))
    return LintResult(score=score, tier=score_tier(score), findings=tuple(findings))


# ---------------------------------------------------------------------------
# 1.4 — per-critic-invocation cost telemetry (D6)


@dataclass(frozen=True)
class CriticCost:
    """One critic-invocation cost record — rides the platform usage plumbing (D6).

    Values-free: identifiers + numbers only (change, critic agent, which pass,
    token counts, USD, ISO timestamp). ``at`` is caller-supplied for determinism.
    """

    change: str
    critic: str
    pass_index: int
    input_tokens: int
    output_tokens: int
    usd: float
    at: str


def record_critic_cost(
    change: str,
    *,
    critic: str,
    pass_index: int,
    input_tokens: int,
    output_tokens: int,
    usd: float,
    at: str,
) -> CriticCost:
    """Build a :class:`CriticCost` for one critic invocation (1.4).

    Validates non-negative counts and a sane ``pass_index`` (1 or 2 — the D2 cap);
    raises :class:`ValueError` otherwise. The record is what the caller emits to
    the usage sink — this module owns the shape, not the transport.
    """
    if pass_index < 1 or pass_index > 2:
        raise ValueError(f"pass_index must be 1 or 2 (D2 cap), got {pass_index}")
    if min(input_tokens, output_tokens) < 0 or usd < 0:
        raise ValueError("token counts and usd must be non-negative")
    return CriticCost(
        change=change,
        critic=critic,
        pass_index=pass_index,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=usd,
        at=at,
    )


def total_critic_cost(records: Iterable[CriticCost]) -> dict[str, Any]:
    """Aggregate critic-cost records: invocation count, tokens, and total USD."""
    recs = list(records)
    return {
        "invocations": len(recs),
        "input_tokens": sum(r.input_tokens for r in recs),
        "output_tokens": sum(r.output_tokens for r in recs),
        "usd": round(sum(r.usd for r in recs), 6),
    }


__all__ = [
    "CRITICAL_FIELDS",
    "DEFAULT_REQUIRED_FIELDS",
    "SCORE_TIERS",
    "CriticCost",
    "LintFinding",
    "LintResult",
    "lint_proposal",
    "record_critic_cost",
    "scan_secrets",
    "score_tier",
    "total_critic_cost",
]
