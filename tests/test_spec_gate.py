"""Tests for otaman_core.spec_gate — Stage-1 lint + critic cost telemetry (JTBD-57 1.1/1.4)."""

from __future__ import annotations

import pytest

from otaman_core.spec_gate import (
    CRITICAL_FIELDS,
    DEFAULT_REQUIRED_FIELDS,
    SCORE_TIERS,
    CriticCost,
    LintResult,
    lint_proposal,
    record_critic_cost,
    scan_secrets,
    score_tier,
    total_critic_cost,
)

REPOS = ["otaman-core", "otaman-cli", "otaman-plugin"]


def _clean_proposal(**over):
    # The lintable shape (ruling A): title/outcome/affected_repos + the seven
    # decision-grade SCR sections (presence + non-placeholder). No artifacts.
    p = {
        "title": "Add a widget",
        "outcome": "JTBD-57",
        "affected_repos": ["otaman-core"],
        "problem": "The widget is missing and users hit the gap daily.",
        "evidence": "Three reports this week; reproduced on main.",
        "impact": "Blocks the common path; medium severity.",
        "direction": "Add the widget behind the existing seam.",
        "scope": "n/a because this is a single-module change.",
        "routing": "otaman-core",
        "workaround": "None; users wait.",
        "body": "A clean proposal body with no secrets and no placeholders.",
    }
    p.update(over)
    return p


# --- score tiers --------------------------------------------------------------


class TestScoreTier:
    def test_five_tiers(self):
        assert len(SCORE_TIERS) == 5

    def test_boundaries(self):
        assert score_tier(100) == "excellent"
        assert score_tier(90) == "excellent"
        assert score_tier(89) == "strong"
        assert score_tier(75) == "strong"
        assert score_tier(50) == "adequate"
        assert score_tier(25) == "weak"
        assert score_tier(0) == "failing"


# --- 1.1 lint -----------------------------------------------------------------


class TestLintClean:
    def test_clean_proposal_scores_100(self):
        r = lint_proposal(_clean_proposal(), platform_repos=REPOS)
        assert isinstance(r, LintResult)
        assert r.score == 100 and r.tier == "excellent" and r.findings == ()

    def test_result_is_advisory_never_raises(self):
        # even total garbage yields a result, never an exception (comment-not-block)
        r = lint_proposal({}, platform_repos=[])
        assert isinstance(r, LintResult)
        assert r.score >= 0


class TestFrontMatterSchema:
    def test_missing_required_fields_flagged(self):
        r = lint_proposal({"title": "x"}, platform_repos=REPOS)
        codes = {f.code for f in r.findings}
        assert codes == {"missing-field"}
        missing = {f.field for f in r.findings}
        # the decision-grade sections + outcome/affected_repos are required; NOT artifacts
        assert "outcome" in missing and "affected_repos" in missing
        assert "problem" in missing and "evidence" in missing  # sections
        assert "artifacts" not in missing

    def test_empty_field_is_missing(self):
        r = lint_proposal(_clean_proposal(outcome="   "), platform_repos=REPOS)
        assert any(f.code == "missing-field" and f.field == "outcome" for f in r.findings)

    def test_required_fields_default(self):
        assert "outcome" in DEFAULT_REQUIRED_FIELDS
        assert "problem" in DEFAULT_REQUIRED_FIELDS  # a decision-grade section
        assert "artifacts" not in DEFAULT_REQUIRED_FIELDS  # dropped (ruling A)


class TestCitations:
    def test_malformed_outcome_warns(self):
        r = lint_proposal(_clean_proposal(outcome="not an id"), platform_repos=REPOS)
        assert any(f.code == "malformed-outcome" and f.level == "warn" for f in r.findings)

    def test_unresolved_outcome_errors_when_registry_given(self):
        r = lint_proposal(
            _clean_proposal(outcome="JTBD-999"), platform_repos=REPOS, known_outcomes=["JTBD-57"]
        )
        assert any(f.code == "unresolved-outcome" and f.level == "error" for f in r.findings)

    def test_resolved_outcome_clean(self):
        r = lint_proposal(
            _clean_proposal(outcome="JTBD-57"), platform_repos=REPOS, known_outcomes=["JTBD-57"]
        )
        assert r.findings == ()

    def test_unresolved_citation_errors(self):
        r = lint_proposal(
            _clean_proposal(change_id="no-such-change"),
            platform_repos=REPOS,
            known_change_ids=["real-change"],
        )
        assert any(f.code == "unresolved-citation" for f in r.findings)

    def test_multi_range_outcome_id_ok(self):
        r = lint_proposal(_clean_proposal(outcome="JTBD-99-102"), platform_repos=REPOS)
        assert not any(f.code == "malformed-outcome" for f in r.findings)


class TestSecretScan:
    def test_aws_key_detected(self):
        body = "here is a key AKIAIOSFODNN7EXAMPLE embedded"
        assert "aws-access-key" in scan_secrets(body)

    def test_github_pat_detected(self):
        body = "token ghp_" + "a" * 36
        assert "github-pat" in scan_secrets(body)

    def test_private_key_block_detected(self):
        assert "private-key-block" in scan_secrets("-----BEGIN RSA PRIVATE KEY-----")

    def test_generic_assignment_detected(self):
        assert "generic-secret-assignment" in scan_secrets('api_key = "s3cr3tvalue12345"')

    def test_clean_body_no_hits(self):
        assert scan_secrets("nothing to see here") == []

    def test_secret_in_body_errors_and_names_code_not_value(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        r = lint_proposal(_clean_proposal(body=f"leaked {secret}"), platform_repos=REPOS)
        sec = [f for f in r.findings if f.code == "secret-in-body"]
        assert sec and all(secret not in f.message for f in sec)  # values-free finding


class TestPlaceholders:
    def test_todo_in_critical_field_errors(self):
        r = lint_proposal(_clean_proposal(title="Add widget TODO"), platform_repos=REPOS)
        assert any(f.code == "placeholder-in-field" and f.field == "title" for f in r.findings)

    def test_tbd_in_a_section(self):
        # a decision-grade section is a critical field — a placeholder in it is flagged
        r = lint_proposal(_clean_proposal(evidence="TBD"), platform_repos=REPOS)
        assert any(f.code == "placeholder-in-field" and f.field == "evidence" for f in r.findings)

    def test_critical_fields_list(self):
        assert "outcome" in CRITICAL_FIELDS

    def test_body_todo_does_not_trip_placeholder(self):
        # placeholders are only flagged in CRITICAL_FIELDS, not the free-text body
        r = lint_proposal(_clean_proposal(body="TODO: expand later"), platform_repos=REPOS)
        assert not any(f.code == "placeholder-in-field" for f in r.findings)


class TestAffectedRepos:
    def test_unknown_repo_errors(self):
        r = lint_proposal(
            _clean_proposal(affected_repos=["otaman-core", "ghost"]), platform_repos=REPOS
        )
        assert any(f.code == "unknown-repo" and "ghost" in f.message for f in r.findings)

    def test_all_known_clean(self):
        r = lint_proposal(
            _clean_proposal(affected_repos=["otaman-core", "otaman-cli"]), platform_repos=REPOS
        )
        assert not any(f.code == "unknown-repo" for f in r.findings)


class TestScoring:
    def test_deducts_per_finding(self):
        # one unknown repo (error, -25) → 75
        r = lint_proposal(_clean_proposal(affected_repos=["ghost"]), platform_repos=REPOS)
        assert r.score == 75 and r.tier == "strong"

    def test_warn_deducts_less_than_error(self):
        r = lint_proposal(_clean_proposal(outcome="bad id"), platform_repos=REPOS)  # warn -10
        assert r.score == 90

    def test_score_clamped_at_zero(self):
        # pile up many errors; score floors at 0, never negative
        bad = {
            "title": "TODO",
            "outcome": "",
            "affected_repos": ["a", "b", "c"],
            "artifacts": "",
            "body": "",
        }
        r = lint_proposal(bad, platform_repos=[])
        assert r.score == 0 and r.tier == "failing"

    def test_deficient_proposal_scenario(self):
        # spec scenario: unresolvable citation + TODO in a critical field →
        # low score with findings returned (still never blocks)
        p = _clean_proposal(change_id="ghost", title="Widget TODO")
        r = lint_proposal(p, platform_repos=REPOS, known_change_ids=["real"])
        codes = {f.code for f in r.findings}
        assert "unresolved-citation" in codes and "placeholder-in-field" in codes
        assert r.score < 75  # visibly deficient, but still just a score (never blocks)


# --- 1.4 critic cost telemetry ------------------------------------------------


class TestCriticCost:
    def test_record(self):
        c = record_critic_cost(
            "my-change",
            critic="plugin-agent",
            pass_index=1,
            input_tokens=1000,
            output_tokens=500,
            usd=0.012,
            at="2026-09-08T10:00:00",
        )
        assert isinstance(c, CriticCost)
        assert c.critic == "plugin-agent" and c.pass_index == 1

    def test_pass_cap_enforced(self):
        with pytest.raises(ValueError):
            record_critic_cost(
                "c", critic="x", pass_index=3, input_tokens=1, output_tokens=1, usd=0.0, at="t"
            )

    def test_pass_index_min(self):
        with pytest.raises(ValueError):
            record_critic_cost(
                "c", critic="x", pass_index=0, input_tokens=1, output_tokens=1, usd=0.0, at="t"
            )

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            record_critic_cost(
                "c", critic="x", pass_index=1, input_tokens=-1, output_tokens=1, usd=0.0, at="t"
            )

    def test_total_aggregates(self):
        recs = [
            record_critic_cost(
                "c", critic="a", pass_index=1, input_tokens=100, output_tokens=50, usd=0.01, at="t"
            ),
            record_critic_cost(
                "c", critic="a", pass_index=2, input_tokens=200, output_tokens=80, usd=0.02, at="t"
            ),
        ]
        agg = total_critic_cost(recs)
        assert agg == {"invocations": 2, "input_tokens": 300, "output_tokens": 130, "usd": 0.03}

    def test_total_empty(self):
        assert total_critic_cost([]) == {
            "invocations": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "usd": 0.0,
        }


# --- proposal_from_scr: the single-home extractor (ruling A 20261001T205124) ---

from otaman_core.spec_gate import proposal_from_scr  # noqa: E402

_SCR_BODY = """## Subject: Harden the dispatch gate

### Problem as observed
The gate fails open on an unparseable file.

### Evidence
Reproduced; five assignments dispatched.

### Impact
High — unapproved work ships.

### Proposed direction
Fail closed on unparseable.

### Scope boundary
n/a because single function.

### Routing
otaman-cli, otaman-core; otaman-specs. Roman-direct session

### Workaround in use
None.
"""


class TestProposalFromScr:
    def test_title_from_subject(self):
        m = proposal_from_scr(_SCR_BODY)
        assert m["title"] == "Harden the dispatch gate"

    def test_explicit_subject_overrides(self):
        assert proposal_from_scr(_SCR_BODY, subject="Override")["title"] == "Override"

    def test_outcome_from_openspec(self):
        m = proposal_from_scr(_SCR_BODY, {"outcome": "JTBD-90"})
        assert m["outcome"] == "JTBD-90"

    def test_affected_repos_from_routing(self):
        m = proposal_from_scr(_SCR_BODY)
        # repo list leads the Routing section; the "Roman-direct session" prose is dropped
        assert m["affected_repos"] == ["otaman-cli", "otaman-core", "otaman-specs"]

    def test_sections_passthrough(self):
        m = proposal_from_scr(_SCR_BODY)
        assert m["problem"].startswith("The gate fails open")
        assert "evidence" in m and "direction" in m

    def test_extracted_scr_lints_without_missing_fields(self):
        m = proposal_from_scr(_SCR_BODY, {"outcome": "JTBD-57"})
        r = lint_proposal(m, platform_repos=["otaman-cli", "otaman-core", "otaman-specs"])
        assert not any(f.code == "missing-field" for f in r.findings)


class TestExtractorCorpusFixes:
    """cli #253 corpus mismatches — body scan, outcome-id, id shape, routing parse."""

    def test_body_is_set_so_secret_scan_runs(self):
        body = "## Subject: x\n\n### Evidence\nleaked ghp_" + "a" * 36 + "\n"
        m = proposal_from_scr(body)
        assert m["body"] == body
        r = lint_proposal(m, platform_repos=[])
        assert any(f.code == "secret-in-body" for f in r.findings)

    def test_outcome_id_preferred_over_prose(self):
        m = proposal_from_scr(
            "## Subject: x\n",
            {"outcome-id": "JTBD-118-interactive-spec-editing", "outcome": "some prose"},
        )
        assert m["outcome"] == "JTBD-118-interactive-spec-editing"

    def test_outcome_falls_back_when_no_id(self):
        m = proposal_from_scr("## Subject: x\n", {"outcome": "JTBD-57"})
        assert m["outcome"] == "JTBD-57"

    def test_id_with_slug_is_not_malformed(self):
        r = lint_proposal(
            _clean_proposal(outcome="JTBD-118-interactive-spec-editing"), platform_repos=REPOS
        )
        assert not any(f.code == "malformed-outcome" for f in r.findings)

    def test_routing_parenthetical_prose_is_not_a_repo(self):
        routing = "otaman-specs (canon delta); otaman-cli (implementation — cli-agent)"
        body = f"## Subject: x\n\n### Routing\n{routing}\n"
        m = proposal_from_scr(body)
        assert m["affected_repos"] == ["otaman-specs", "otaman-cli"]  # cli-agent dropped
