"""shared-agent-memory 1.1 — the knowledge entry schema + single-home IO.

`.agents/knowledge/` holds durable operational facts. These pin the entry
contract (typed frontmatter, the evidence-anchor rule, past-due review-by) and
the round-trip through the one reader/writer, which reuses the kernel's own
frontmatter parser and slugifier.
"""

from __future__ import annotations

from pathlib import Path

from otaman_core.knowledge import (
    KIND_DECISION,
    KIND_LESSON,
    KINDS,
    KnowledgeEntry,
    anchor_is_recognized,
    is_past_due,
    load_entries,
    parse_entry,
    render_entry,
    validate_entry,
    write_entry,
)


def _entry(**over) -> KnowledgeEntry:
    base = dict(
        type=KIND_LESSON,
        author="core-agent",
        created="2026-09-23",
        review_by="2026-12-23",
        anchor="src/otaman_core/spawn.py:88",
        title="worktree sessions need main-tree identity",
        body="A worktree session must resolve identity via its main working tree.",
    )
    base.update(over)
    return KnowledgeEntry(**base)


# ---------------------------------------------------------------------------
# schema + validation


def test_the_four_kinds():
    assert KINDS == ("fact", "lesson", "decision", "reference")


def test_a_well_formed_entry_validates():
    assert validate_entry(_entry()) == []


def test_missing_anchor_is_refused_naming_the_rule():
    errs = validate_entry(_entry(anchor="  "))
    assert any("anchor" in e and "provenance" in e for e in errs)


def test_unknown_type_is_refused():
    assert any("type must be one of" in e for e in validate_entry(_entry(type="note")))


def test_author_and_title_required():
    assert any("author is required" in e for e in validate_entry(_entry(author="")))
    assert any("title is required" in e for e in validate_entry(_entry(title="")))


def test_dates_must_be_iso():
    assert any(
        "created must be an ISO date" in e for e in validate_entry(_entry(created="Sept 23"))
    )
    assert any(
        "review-by must be an ISO date" in e for e in validate_entry(_entry(review_by="soon"))
    )


def test_anchor_recognition_is_advisory():
    assert anchor_is_recognized("src/x.py:12")
    assert anchor_is_recognized("20260921T150141-human-to-all-x")
    assert anchor_is_recognized("87% of the queue")  # a measured number
    assert not anchor_is_recognized("just prose, no anchor")


# ---------------------------------------------------------------------------
# past-due review-by


def test_past_due_is_flagged():
    assert is_past_due(_entry(review_by="2026-09-01"), today="2026-09-23") is True


def test_future_review_by_is_not_past_due():
    assert is_past_due(_entry(review_by="2026-12-23"), today="2026-09-23") is False


def test_review_by_equal_to_today_is_not_past_due():
    assert is_past_due(_entry(review_by="2026-09-23"), today="2026-09-23") is False


def test_malformed_review_by_is_not_past_due():
    assert is_past_due(_entry(review_by="whenever"), today="2026-09-23") is False


# ---------------------------------------------------------------------------
# render / parse round-trip


def test_render_carries_every_field():
    text = render_entry(_entry())
    for token in (
        "type: lesson",
        "author: core-agent",
        "created: 2026-09-23",
        "review-by: 2026-12-23",
        "anchor: src/otaman_core/spawn.py:88",
    ):
        assert token in text
    assert "worktree session must resolve" in text


def test_round_trips_through_parse():
    entry = _entry()
    parsed = parse_entry(render_entry(entry))
    assert parsed == entry


def test_parse_reads_underscored_review_by_alias():
    text = (
        "---\ntype: fact\nauthor: a\ncreated: 2026-09-23\n"
        "review_by: 2026-12-01\nanchor: x:1\ntitle: t\n---\nbody\n"
    )
    parsed = parse_entry(text)
    assert parsed is not None and parsed.review_by == "2026-12-01"


def test_parse_returns_none_for_a_non_entry_file():
    assert parse_entry("just a README, no frontmatter\n") is None


def test_stem_is_created_plus_slug():
    assert _entry().stem == "2026-09-23-worktree-sessions-need-main-tree-identity"


# ---------------------------------------------------------------------------
# IO


def test_write_then_load_round_trip(tmp_path: Path):
    kd = tmp_path / "knowledge"
    path = write_entry(kd, _entry())
    assert path.name == "2026-09-23-worktree-sessions-need-main-tree-identity.md"
    (loaded,) = load_entries(kd)
    assert loaded == _entry()


def test_load_entries_missing_dir_is_empty(tmp_path: Path):
    assert load_entries(tmp_path / "nope") == []


def test_load_entries_skips_a_non_entry_readme(tmp_path: Path):
    kd = tmp_path / "knowledge"
    kd.mkdir()
    (kd / "README.md").write_text("# knowledge\nHow this pile works.\n", encoding="utf-8")
    write_entry(kd, _entry())
    entries = load_entries(kd)
    assert len(entries) == 1 and entries[0].title.startswith("worktree")


def test_load_entries_sorted_by_created_then_title(tmp_path: Path):
    kd = tmp_path / "knowledge"
    write_entry(kd, _entry(created="2026-09-25", title="zeta", anchor="x:1"))
    write_entry(kd, _entry(created="2026-09-24", title="alpha", anchor="x:1"))
    write_entry(kd, _entry(created="2026-09-24", title="beta", anchor="x:1"))
    got = [(e.created, e.title) for e in load_entries(kd)]
    assert got == [("2026-09-24", "alpha"), ("2026-09-24", "beta"), ("2026-09-25", "zeta")]


def test_rewrite_same_stem_is_a_correction_not_a_duplicate(tmp_path: Path):
    kd = tmp_path / "knowledge"
    write_entry(kd, _entry(body="first take"))
    write_entry(kd, _entry(body="corrected take"))
    entries = load_entries(kd)
    assert len(entries) == 1 and entries[0].body == "corrected take"


def test_a_decision_entry_is_a_valid_kind():
    assert validate_entry(_entry(type=KIND_DECISION, anchor="20260921T152814-human-to-all")) == []
