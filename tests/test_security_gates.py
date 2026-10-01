"""security-gates-hook-c 1.1 — the security-gates: config model, parser, resolver.

Pins the five-layer ladder, the 2-of-3 scanner-pair rule, per-language defaults
unioned for mixed repos, per-repo overrides, and the visible per-repo opt-out.
"""

from __future__ import annotations

import pytest

from otaman_core.security_gates import (
    LAYERS,
    RELOCATION_NOTE,
    SCANNER_PAIR_SIZE,
    SCANNERS,
    SecurityGatesError,
    parse_security_gates,
    resolve_repo_gates,
)

_BLOCK = {
    "languages": {
        "python": {
            "pre-commit": {"tools": ["ruff", "mypy"], "blocking": True},
            "ci-fast": {"tools": ["gitleaks", "bandit"], "timeout": 30, "blocking": True},
            "ci-medium": {
                "tools": ["semgrep"],
                "scanner-pair": ["trivy", "grype"],
                "timeout": 300,
                "blocking": True,
            },
            "ci-slow": {"tools": ["semgrep-deep"], "blocking": False},
            "llm-observer": {"blocking": False, "cost-cap": 0.5},
        },
        "javascript": {
            "ci-fast": {"tools": ["gitleaks", "npm-audit"], "timeout": 45, "blocking": True},
            "ci-medium": {"tools": ["semgrep-js"], "blocking": True},
        },
    },
    "repos": {
        "backend": {
            "languages": ["python"],
            "ci-medium": {"scanner-pair": ["trivy", "osv-scanner"]},
            "ci-slow": {"opt-in": True},  # this repo opts into the advisory layer
        },
        "web": {"languages": ["python", "javascript"]},  # does NOT opt into ci-slow
        "docs": {"opt-out": True},
    },
}


# --- constants ---------------------------------------------------------------


def test_layers_and_scanners_are_canon():
    assert LAYERS == ("pre-commit", "ci-fast", "ci-medium", "ci-slow", "llm-observer")
    assert SCANNERS == ("trivy", "grype", "osv-scanner")
    assert SCANNER_PAIR_SIZE == 2


# --- parse -------------------------------------------------------------------


def test_parse_empty_and_none():
    assert parse_security_gates(None).languages == {}
    assert parse_security_gates({}).repos == {}
    assert parse_security_gates(None).relocation_note == RELOCATION_NOTE


def test_parse_full_block():
    cfg = parse_security_gates(_BLOCK)
    assert set(cfg.languages) == {"python", "javascript"}
    assert cfg.languages["python"]["ci-medium"].scanner_pair == ("trivy", "grype")
    assert cfg.languages["python"]["ci-fast"].timeout == 30
    assert cfg.repos["docs"]["opt-out"] is True


@pytest.mark.parametrize(
    "block",
    [
        {"languages": {"py": {"ci-instant": {"tools": []}}}},  # unknown layer
        {"languages": {"py": {"ci-fast": {"toolz": []}}}},  # unknown field
        {"languages": {"py": {"ci-fast": {"tools": "ruff"}}}},  # tools not a list
        {"languages": {"py": {"ci-fast": {"timeout": -1}}}},  # negative timeout
        {"languages": {"py": {"ci-medium": {"scanner-pair": ["trivy"]}}}},  # not 2
        {"languages": {"py": {"ci-medium": {"scanner-pair": ["trivy", "trivy"]}}}},  # dup
        {"languages": {"py": {"ci-medium": {"scanner-pair": ["trivy", "snyk"]}}}},  # unknown
        {"languages": {"py": {"ci-fast": {"scanner-pair": ["trivy", "grype"]}}}},  # wrong layer
        {"languages": {"py": {"ci-slow": {"opt-in": True}}}},  # opt-in in a language default
        {"repos": {"r": {"ci-fast": {"opt-in": True}}}},  # opt-in on a non-ci-slow layer
        {"repos": {"r": {"opt_out": True}}},  # stray key (underscore)
        {"languages": "nope"},  # not a mapping
    ],
)
def test_parse_rejects_malformed(block):
    with pytest.raises(SecurityGatesError):
        parse_security_gates(block)


# --- resolve -----------------------------------------------------------------


def test_resolve_single_language():
    cfg = parse_security_gates(_BLOCK)
    gates = resolve_repo_gates(cfg, "backend")
    assert gates.opt_out is False
    assert gates.languages == ("python",)
    # per-repo override wins the recorded pair
    assert gates.layer("ci-medium").scanner_pair == ("trivy", "osv-scanner")
    assert gates.layer("ci-fast").tools == ("gitleaks", "bandit")
    # this repo opted into ci-slow; the resolved opt_in answers "did THIS repo opt in?"
    assert gates.layer("ci-slow").opt_in is True
    # layers come back in canonical order
    assert [la.layer for la in gates.layers] == [n for n in LAYERS if gates.layer(n) is not None]


def test_resolve_mixed_repo_unions_tools():
    cfg = parse_security_gates(_BLOCK)
    gates = resolve_repo_gates(cfg, "web")
    ci_fast = gates.layer("ci-fast")
    # union across python + javascript, order-stable and deduped
    assert ci_fast.tools == ("gitleaks", "bandit", "npm-audit")
    # timeout is the max across languages (30 vs 45)
    assert ci_fast.timeout == 45
    # blocking is any-true
    assert ci_fast.blocking is True
    # web did NOT opt into ci-slow — opt_in is False (not monotonic from a default)
    assert gates.layer("ci-slow").opt_in is False


def test_resolve_opt_out_is_visible_and_empty():
    cfg = parse_security_gates(_BLOCK)
    gates = resolve_repo_gates(cfg, "docs")
    assert gates.opt_out is True
    assert gates.layers == ()


def test_resolve_unknown_repo_uses_caller_languages():
    cfg = parse_security_gates(_BLOCK)
    gates = resolve_repo_gates(cfg, "new-repo", languages=("python",))
    assert gates.layer("pre-commit").tools == ("ruff", "mypy")


def test_resolve_repo_with_no_languages_has_no_layers():
    cfg = parse_security_gates(_BLOCK)
    gates = resolve_repo_gates(cfg, "unlisted")
    assert gates.opt_out is False
    assert gates.layers == ()
