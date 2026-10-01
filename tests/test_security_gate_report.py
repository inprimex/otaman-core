"""security-gates-hook-c 1.6 — the security-gate-report schema.

Pins the per-layer verdicts, the D3 disagreement flags, the suppression audit, the
one derived blocked rule (fail verdict OR unjustified suppression), and round-trip
(de)serialization. The VALID_TYPES registration is checked in test_validate_message.
"""

from __future__ import annotations

import pytest

from otaman_core.security_gate_report import (
    VERDICTS,
    Disagreement,
    LayerVerdict,
    SecurityGateReport,
    SecurityGateReportError,
    Suppression,
    is_blocked,
    report_from_dict,
    report_to_dict,
)


def _report(**kw) -> SecurityGateReport:
    base = {"repo": "otaman-core"}
    base.update(kw)
    return SecurityGateReport(**base)


# --- blocked derivation ------------------------------------------------------


def test_clean_report_does_not_block():
    r = _report(layers=(LayerVerdict("ci-fast", "pass"), LayerVerdict("ci-medium", "pass")))
    assert is_blocked(r) is False
    assert r.blocked is False


def test_fail_verdict_blocks():
    r = _report(layers=(LayerVerdict("ci-medium", "fail", ("semgrep: sql-injection",)),))
    assert r.blocked is True


def test_advisory_never_blocks():
    # ci-slow / llm-observer are advisory by construction (D3)
    r = _report(
        layers=(LayerVerdict("ci-slow", "advisory"), LayerVerdict("llm-observer", "advisory"))
    )
    assert r.blocked is False


def test_unjustified_suppression_blocks():
    r = _report(suppressions=(Suppression("# nosemgrep", "a.py:10", justified=False),))
    assert r.blocked is True
    assert r.unjustified_suppressions == r.suppressions


def test_justified_suppression_does_not_block():
    r = _report(
        suppressions=(
            Suppression(
                "# nosemgrep", "a.py:10", justified=True, justification="false positive, see #123"
            ),
        )
    )
    assert r.blocked is False
    assert r.unjustified_suppressions == ()


def test_disagreement_recorded_but_blocking_comes_from_the_fail():
    # D3: deterministic vulnerable + observer safe -> the fail verdict blocks; the
    # disagreement is recorded for triage, it is not itself the blocking signal
    r = _report(
        layers=(LayerVerdict("ci-medium", "fail", ("semgrep: xss",)),),
        disagreements=(Disagreement("xss in render", "vulnerable", "safe", layer="ci-medium"),),
    )
    assert r.blocked is True
    assert r.disagreements[0].deterministic == "vulnerable"


# --- parse / validate --------------------------------------------------------


def test_round_trip():
    r = _report(
        pr="otaman-core#92",
        layers=(LayerVerdict("ci-fast", "pass"), LayerVerdict("ci-medium", "fail", ("x",))),
        disagreements=(Disagreement("f", "vulnerable", "safe", layer="ci-medium"),),
        suppressions=(Suppression("# nosemgrep", "a.py:1", justified=True, justification="ok"),),
    )
    assert report_from_dict(report_to_dict(r)) == r


def test_to_dict_includes_derived_blocked():
    r = _report(layers=(LayerVerdict("ci-medium", "fail"),))
    assert report_to_dict(r)["blocked"] is True


def test_minimal_report_parses():
    assert report_from_dict({"repo": "r"}) == SecurityGateReport(repo="r")


@pytest.mark.parametrize(
    "data",
    [
        {},  # no repo
        {"repo": ""},  # empty repo
        {"repo": "r", "layers": [{"layer": "ci-instant", "verdict": "pass"}]},  # bad layer
        {"repo": "r", "layers": [{"layer": "ci-fast", "verdict": "green"}]},  # bad verdict
        {"repo": "r", "layers": [{"layer": "ci-fast"}]},  # missing verdict
        {"repo": "r", "layers": "nope"},  # layers not a list
        {"repo": "r", "suppressions": [{"marker": "x"}]},  # suppression missing location
        {
            "repo": "r",
            "disagreements": [{"finding": "f", "deterministic": "v"}],
        },  # missing observer
        {"repo": "r", "pr": 92},  # pr not a string
    ],
)
def test_rejects_malformed(data):
    with pytest.raises(SecurityGateReportError):
        report_from_dict(data)


def test_verdicts_canon():
    assert VERDICTS == ("pass", "fail", "advisory", "skipped", "not-run")
