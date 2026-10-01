"""The ``security-gate-report`` — Hook C's own result record (sghc 1.6).

Hook C's result gets its OWN surface, distinct from JTBD-57's spec-proposal critique
(ruling 20260930T210003): a per-PR record of what the five-layer ladder found,
attached to the PR/review flow. This module is the single home (two-consumer rule)
for its schema — the plugin resolution function EMITS it (sghc 1.7) and the cli
renders it in the review flow (1.7). ``security-gate-report`` is registered in
``validate_message.VALID_TYPES`` so the record can ride the bus.

Three parts, from the design:

- **per-layer verdicts** — one :class:`LayerVerdict` per ladder layer that ran
  (:data:`otaman_core.security_gates.LAYERS`), each ``pass`` / ``fail`` /
  ``advisory`` / ``skipped`` / ``not-run``.
- **disagreement flags** — a deterministic *vulnerable* verdict the observer called
  *safe* (design D3). The deterministic verdict STANDS and blocks; the dissent is
  recorded for human triage, never silently resolved.
- **suppression audit** — each in-diff suppression and whether it carries the
  required justification. A bare marker (``# nosemgrep`` with no comment) is
  unjustified and fails the gate.

The gate verdict is derived, not asserted: :func:`is_blocked` blocks iff any layer
``fail`` or any UNJUSTIFIED suppression — one rule, so the emitter (plugin) and the
renderer (cli) cannot disagree on whether a report blocks.

Pure: dataclasses + dict (de)serialization for the bus body / an artifact. No I/O,
no tool execution (the layers run elsewhere; this records their result).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otaman_core.security_gates import LAYERS

#: Per-layer verdict values. ``pass``: ran clean. ``fail``: a BLOCKING finding.
#: ``advisory``: a non-blocking finding (ci-slow / llm-observer never block — D3).
#: ``skipped``: opt-out or not configured for the repo. ``not-run``: did not execute
#: (e.g. the llm-observer cost cap was hit). Only ``fail`` blocks.
VERDICTS: tuple[str, ...] = ("pass", "fail", "advisory", "skipped", "not-run")

#: The one blocking verdict — a layer that found something that stops the PR.
BLOCKING_VERDICT = "fail"


class SecurityGateReportError(ValueError):
    """A malformed security-gate-report payload."""


@dataclass(frozen=True)
class LayerVerdict:
    """One ladder layer's outcome. ``findings`` are short human-readable lines."""

    layer: str
    verdict: str
    findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Disagreement:
    """A deterministic-vs-observer conflict (design D3) — the deterministic stands.

    ``finding`` identifies what was flagged; ``deterministic`` is the blocking tool
    verdict; ``observer`` is the LLM's dissenting call. Recorded for human triage,
    never silently reconciled.
    """

    finding: str
    deterministic: str
    observer: str
    layer: str = ""


@dataclass(frozen=True)
class Suppression:
    """One in-diff suppression and whether it is justified.

    ``justified`` is True only when the tool marker is paired with a justifying
    comment; a bare marker is unjustified and fails the gate (no-silent-success
    applied to suppressions).
    """

    marker: str
    location: str
    justified: bool
    justification: str | None = None


@dataclass(frozen=True)
class SecurityGateReport:
    """Hook C's per-PR result: layer verdicts, disagreements, suppression audit."""

    repo: str
    pr: str | None = None
    layers: tuple[LayerVerdict, ...] = ()
    disagreements: tuple[Disagreement, ...] = ()
    suppressions: tuple[Suppression, ...] = ()

    @property
    def unjustified_suppressions(self) -> tuple[Suppression, ...]:
        """Suppressions with no justification — each one fails the gate."""
        return tuple(s for s in self.suppressions if not s.justified)

    @property
    def blocked(self) -> bool:
        """Whether this report blocks the PR (see :func:`is_blocked`)."""
        return is_blocked(self)


def is_blocked(report: SecurityGateReport) -> bool:
    """Whether *report* blocks the PR — the one derivation both emitter and renderer use.

    Blocks iff any layer verdict is :data:`BLOCKING_VERDICT` (a deterministic
    vulnerable finding stands and blocks, design D3) OR any suppression is
    unjustified (a bare marker fails the gate). ``advisory`` findings — ci-slow and
    the llm-observer — never block.
    """
    if any(la.verdict == BLOCKING_VERDICT for la in report.layers):
        return True
    return bool(report.unjustified_suppressions)


def _as_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise SecurityGateReportError(f"{where} must be a non-empty string")
    return value


def _str_tuple(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
        raise SecurityGateReportError(f"{where} must be a list of strings")
    return tuple(value)


def _parse_layer(raw: Any, where: str) -> LayerVerdict:
    if not isinstance(raw, dict):
        raise SecurityGateReportError(f"{where} must be a mapping")
    layer = _as_str(raw.get("layer"), f"{where}.layer")
    if layer not in LAYERS:
        raise SecurityGateReportError(
            f"{where}.layer: unknown layer {layer!r}; layers are {', '.join(LAYERS)}"
        )
    verdict = _as_str(raw.get("verdict"), f"{where}.verdict")
    if verdict not in VERDICTS:
        raise SecurityGateReportError(
            f"{where}.verdict: unknown verdict {verdict!r}; one of {', '.join(VERDICTS)}"
        )
    return LayerVerdict(
        layer=layer, verdict=verdict, findings=_str_tuple(raw.get("findings"), where)
    )


def _parse_disagreement(raw: Any, where: str) -> Disagreement:
    if not isinstance(raw, dict):
        raise SecurityGateReportError(f"{where} must be a mapping")
    return Disagreement(
        finding=_as_str(raw.get("finding"), f"{where}.finding"),
        deterministic=_as_str(raw.get("deterministic"), f"{where}.deterministic"),
        observer=_as_str(raw.get("observer"), f"{where}.observer"),
        layer=raw.get("layer", "") if isinstance(raw.get("layer", ""), str) else "",
    )


def _parse_suppression(raw: Any, where: str) -> Suppression:
    if not isinstance(raw, dict):
        raise SecurityGateReportError(f"{where} must be a mapping")
    justification = raw.get("justification")
    if justification is not None and not isinstance(justification, str):
        raise SecurityGateReportError(f"{where}.justification must be a string or absent")
    return Suppression(
        marker=_as_str(raw.get("marker"), f"{where}.marker"),
        location=_as_str(raw.get("location"), f"{where}.location"),
        justified=bool(raw.get("justified", False)),
        justification=justification,
    )


def report_from_dict(data: Any) -> SecurityGateReport:
    """Parse and validate a security-gate-report payload (bus body / artifact)."""
    if not isinstance(data, dict):
        raise SecurityGateReportError("security-gate-report must be a mapping")
    pr = data.get("pr")
    if pr is not None and not isinstance(pr, str):
        raise SecurityGateReportError("pr must be a string or absent")
    layers = data.get("layers") or []
    disagreements = data.get("disagreements") or []
    suppressions = data.get("suppressions") or []
    for name, seq in (
        ("layers", layers),
        ("disagreements", disagreements),
        ("suppressions", suppressions),
    ):
        if not isinstance(seq, list):
            raise SecurityGateReportError(f"{name} must be a list")
    return SecurityGateReport(
        repo=_as_str(data.get("repo"), "repo"),
        pr=pr,
        layers=tuple(_parse_layer(x, f"layers[{i}]") for i, x in enumerate(layers)),
        disagreements=tuple(
            _parse_disagreement(x, f"disagreements[{i}]") for i, x in enumerate(disagreements)
        ),
        suppressions=tuple(
            _parse_suppression(x, f"suppressions[{i}]") for i, x in enumerate(suppressions)
        ),
    )


def report_to_dict(report: SecurityGateReport) -> dict[str, Any]:
    """Serialize a report to a plain dict (for the bus body / an artifact).

    Includes the derived ``blocked`` verdict so a reader that does not recompute it
    still sees the gate outcome; :func:`report_from_dict` ignores it and re-derives.
    """
    out: dict[str, Any] = {"repo": report.repo, "blocked": report.blocked}
    if report.pr is not None:
        out["pr"] = report.pr
    out["layers"] = [
        {
            "layer": la.layer,
            "verdict": la.verdict,
            **({"findings": list(la.findings)} if la.findings else {}),
        }
        for la in report.layers
    ]
    out["disagreements"] = [
        {
            "finding": d.finding,
            "deterministic": d.deterministic,
            "observer": d.observer,
            **({"layer": d.layer} if d.layer else {}),
        }
        for d in report.disagreements
    ]
    out["suppressions"] = [
        {
            "marker": s.marker,
            "location": s.location,
            "justified": s.justified,
            **({"justification": s.justification} if s.justification is not None else {}),
        }
        for s in report.suppressions
    ]
    return out


__all__ = [
    "BLOCKING_VERDICT",
    "VERDICTS",
    "Disagreement",
    "LayerVerdict",
    "SecurityGateReport",
    "SecurityGateReportError",
    "Suppression",
    "is_blocked",
    "report_from_dict",
    "report_to_dict",
]
