"""console-messages-triage 1.1 — the review-policy schema + classification.

Pins the three classes, the shipped default (decisions mandatory, task-complete
auto-triage), per-program override with a named source, urgent-always-mandatory, and
the mandatory/auto-triage conflict refusal.
"""

from __future__ import annotations

import pytest

from otaman_core.review_policy import (
    AUTO_TRIAGE,
    CLASSES,
    MANDATORY,
    OPT_IN,
    ReviewPolicyError,
    classify,
    default_policy,
    parse_review_policy,
)


def test_classes_canon_in_render_order():
    assert CLASSES == ("mandatory", "opt-in", "auto-triage")


# --- shipped default ---------------------------------------------------------


def test_default_makes_decisions_mandatory():
    p = default_policy()
    assert classify(p, "decision-required") == MANDATORY
    assert classify(p, "spec-change-request") == MANDATORY
    assert classify(p, "spec-approval-pending") == MANDATORY


def test_default_collapses_task_complete_and_noise():
    p = default_policy()
    assert classify(p, "task-complete") == AUTO_TRIAGE
    assert classify(p, "announce") == AUTO_TRIAGE
    assert classify(p, "outcome-status-changed") == AUTO_TRIAGE


def test_default_unnamed_type_is_opt_in():
    assert classify(default_policy(), "question") == OPT_IN
    assert default_policy().source == "shipped-default"


def test_urgent_priority_is_always_mandatory():
    p = default_policy()
    # even an auto-triage type, at urgent priority, must not be collapsed
    assert classify(p, "task-complete", priority="urgent") == MANDATORY
    assert classify(p, "question", priority="urgent") == MANDATORY


# --- per-program override ----------------------------------------------------


def test_program_can_promote_a_type_to_mandatory():
    p = parse_review_policy({"mandatory": ["review-request"]})
    assert p.source == "program"
    assert classify(p, "review-request") == MANDATORY
    # removing the declaration returns it to opt-in (default)
    assert classify(default_policy(), "review-request") == OPT_IN


def test_program_none_is_the_default():
    assert parse_review_policy(None).source == "shipped-default"


def test_conflict_between_mandatory_and_auto_triage_refused():
    with pytest.raises(ReviewPolicyError, match="both mandatory and auto-triage"):
        parse_review_policy({"mandatory": ["info"], "auto-triage": ["info"]})


@pytest.mark.parametrize(
    "block",
    [
        "nope",  # not a mapping
        {"mandatory": "decision-required"},  # not a list
        {"unknown-key": []},  # stray key
        {"auto-triage": [1, 2]},  # non-string entries
    ],
)
def test_rejects_malformed(block):
    with pytest.raises(ReviewPolicyError):
        parse_review_policy(block)
