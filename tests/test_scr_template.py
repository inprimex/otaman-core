"""generated-artifact-quality — one SCR template, refused when hollow.

Measured: 54 of 119 SCRs ever filed (45%) carry unfilled TODO sections. The
team-mode SCR — the largest change of its week — was APPROVED with three of
four sections reading TODO, and produced two day-one implementation blockers a
decision-grade request would have settled. The template lived in two places and
neither refused an empty section.

The refusal is only fair because `n/a because <reason>` is always available: the
standard forbids silence, it never forces invention.

These are the pure-logic tests for the shared module now hosted in otaman-core
(generated-artifact-quality 1.2 re-home). The CLI- and plugin-transport wiring
tests stay in their own repos against the re-exports.
"""

from __future__ import annotations

import pytest

from otaman_core.scr_template import (
    EVIDENCE_LEVELS,
    SECTION_KEYS,
    SECTIONS,
    completeness,
    completeness_line,
    has_template,
    is_hollow,
    render,
    unfilled_sections,
    validate,
    validate_evidence_level,
)

FULL = {
    "problem": "564 review-requests land on the human",
    "evidence": "src/otaman_cli/console/inbox.py:45; bus stem 20260916T221457",
    "impact": "87% of the queue; every reader of it",
    "direction": "a write-time recipient rule",
    "scope": "does not change console default filters",
    "routing": "otaman-cli; spec-agent decides",
    "workaround": "n/a because the queue still reads",
}


# ---------------------------------------------------------------------------
# 1.1 — the seven sections


def test_there_are_exactly_seven_decision_grade_sections():
    assert len(SECTIONS) == 7


def test_the_sections_are_the_ones_the_proposal_names():
    assert SECTION_KEYS == (
        "problem",
        "evidence",
        "impact",
        "direction",
        "scope",
        "routing",
        "workaround",
    )


def test_render_emits_every_section():
    body = render("a title", sections=FULL)
    for section in SECTIONS:
        assert f"### {section.heading}" in body


def test_an_unfilled_section_renders_its_guidance_not_a_bare_todo():
    """A prompt tells the author what would make the section answerable; a bare
    `TODO:` is what the old template shipped and what 45% of SCRs kept."""
    body = render("a title")
    assert "TODO: What you actually saw" in body


def test_evidence_level_is_rendered_when_declared():
    assert "**Evidence level**: measured" in render("t", sections=FULL, evidence_level="measured")


def test_no_evidence_level_line_when_undeclared():
    assert "Evidence level" not in render("t", sections=FULL)


# ---------------------------------------------------------------------------
# 1.1 — the refusal


def test_a_fully_filled_scr_passes():
    ok, errors = validate(render("t", sections=FULL))
    assert ok, errors


def test_an_empty_scr_is_refused_naming_every_section():
    ok, errors = validate(render("t"))
    assert not ok
    joined = " ".join(errors)
    for section in SECTIONS:
        assert section.heading in joined, section.heading


def test_the_refusal_names_only_the_offending_sections():
    """ "Your SCR is incomplete" sends the author back to the template; naming
    the sections tells them what to type."""
    ok, errors = validate(render("t", sections={**FULL, "impact": "TODO: later"}))
    assert not ok
    assert "Impact" in errors[0]
    assert "Evidence," not in errors[0]  # the filled ones are not listed


def test_n_a_because_counts_as_filled():
    """The escape hatch that makes the refusal fair — an explicit, reviewable
    claim that the section does not apply."""
    body = render("t", sections={**FULL, "workaround": "n/a because nothing is needed"})
    assert validate(body)[0]


def test_n_a_without_a_reason_is_still_unfilled():
    """`n/a` alone is silence with extra steps."""
    body = render("t", sections={**FULL, "workaround": "n/a"})
    assert not validate(body)[0]
    body2 = render("t", sections={**FULL, "workaround": "n/a because"})
    assert not validate(body2)[0]


@pytest.mark.parametrize("marker", ["TODO", "TBD", "FIXME", "???", "xxx"])
def test_placeholder_shapes_are_all_caught(marker):
    body = render("t", sections={**FULL, "impact": f"{marker} fill this in"})
    assert "Impact" in unfilled_sections(body)


def test_a_section_merely_mentioning_todo_later_on_is_filled():
    """Only the FIRST line decides — a real answer that happens to mention a
    TODO elsewhere is not a placeholder."""
    body = render("t", sections={**FULL, "impact": "Affects 87%.\nTODO: measure again next week"})
    assert "Impact" not in unfilled_sections(body)


@pytest.mark.parametrize("level", EVIDENCE_LEVELS)
def test_every_declared_evidence_level_is_accepted(level):
    assert validate_evidence_level(level)[0]


def test_an_unknown_evidence_level_is_refused_naming_the_options():
    ok, err = validate_evidence_level("very-sure")
    assert not ok
    for level in EVIDENCE_LEVELS:
        assert level in err


def test_no_evidence_level_is_allowed():
    assert validate_evidence_level(None)[0]
    assert validate_evidence_level("")[0]


# ---------------------------------------------------------------------------
# 1.3 — completeness is a FACT, never a score


def test_completeness_reports_counts_not_a_score():
    facts = completeness(render("t", sections=FULL))
    assert facts["sections_filled"] == 7
    assert facts["sections_total"] == 7
    assert "score" not in facts
    assert not any(isinstance(v, float) for v in facts.values())


def test_the_line_carries_no_percentage_or_score_word():
    """A number would invite ranking SCRs by it — refused until an independent
    critic exists to produce one."""
    line = completeness_line(render("t", sections={"problem": "x"}))
    assert "%" not in line
    assert "score" not in line.lower()
    assert "confidence" not in line.lower()


def test_the_line_names_what_is_missing():
    line = completeness_line(render("t", sections={"problem": "x", "evidence": "y"}))
    assert "2/7 filled" in line
    assert "Impact" in line


def test_anchors_are_counted():
    body = render(
        "t",
        sections={**FULL, "evidence": "src/otaman_cli/console/app.py:715 and 20260916T213147-x"},
    )
    assert completeness(body)["anchors"] >= 2


def test_declared_evidence_level_is_surfaced():
    assert "evidence: measured" in completeness_line(
        render("t", sections=FULL, evidence_level="measured")
    )


# ---------------------------------------------------------------------------
# legacy bodies must not be slandered


def test_a_legacy_body_is_reported_as_legacy_not_as_empty():
    """Every SCR filed before this change uses the OLD five headings. Measuring
    them against the seven new ones reported 0/7 for well-written requests —
    a false accusation, and the confidently-wrong-surface failure that trains
    readers to ignore the line. Found by running this against the live bus."""
    legacy = (
        "## Subject: Spec change request: a real one\n\n"
        "### What needs to change\nSomething concrete.\n\n"
        "### Why this is needed\nA measured reason.\n"
    )
    assert has_template(legacy) is False
    line = completeness_line(legacy)
    assert "legacy format" in line
    assert "0/7" not in line


def test_a_template_body_is_detected_as_such():
    assert has_template(render("t", sections=FULL)) is True


def test_legacy_still_counts_anchors():
    legacy = "### What needs to change\nsee src/otaman_cli/main.py:12\n"
    assert completeness(legacy)["anchors"] == 1


# ---------------------------------------------------------------------------
# is_hollow — the shared-door refusal


@pytest.mark.parametrize(
    "body",
    ["", "TODO\n", "### Heading only\n", "n/a\n", "### A\nTODO: x\n### B\n???\n"],
)
def test_bodies_that_answer_nothing_are_hollow(body):
    assert is_hollow(body)[0] is True


@pytest.mark.parametrize(
    "body",
    ["Real content.\n", "### What needs to change\nA measured problem.\n"],
)
def test_bodies_with_substance_are_not_hollow(body):
    assert is_hollow(body)[0] is False
