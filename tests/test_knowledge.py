"""shared-agent-memory 1.1 — the knowledge entry schema + single-home IO.

`.agents/knowledge/` holds durable operational facts. These pin the entry
contract (typed frontmatter, the evidence-anchor rule, past-due review-by) and
the round-trip through the one reader/writer, which reuses the kernel's own
frontmatter parser and slugifier.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_core.knowledge import (
    AMENDED_FLAG,
    FUNCTION_DEVELOPMENT,
    FUNCTION_RESEARCH,
    FUNCTIONS,
    KIND_DECISION,
    KIND_LESSON,
    KINDS,
    STATE_ACTIVE,
    STATE_DORMANT,
    STATES,
    KnowledgeEntry,
    KnowledgeError,
    active_entries,
    amend_entry,
    anchor_is_recognized,
    in_partition,
    index_line,
    is_amended,
    is_past_due,
    load_entries,
    load_entry_by_stem,
    mark_accessed,
    parse_entry,
    render_entry,
    set_state,
    superseded_by,
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
        function=FUNCTION_DEVELOPMENT,
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
    for key in ("type:", "author:", "created:", "review-by:", "anchor:", "title:"):
        assert key in text
    assert "worktree session must resolve" in text


def test_title_with_hash_survives_round_trip():
    """REGRESSION (cli-agent 20260923T074504): unquoted frontmatter let YAML read
    `#` as a comment and silently truncate the title — `worktree ... #73` lost
    the `#73`. The renderer must quote values so nothing is dropped."""
    entry = _entry(title="worktree sessions need core #73")
    parsed = parse_entry(render_entry(entry))
    assert parsed is not None and parsed.title == "worktree sessions need core #73"


def test_title_with_colon_survives_round_trip():
    entry = _entry(title="worktree: owner resolution needs the main tree")
    parsed = parse_entry(render_entry(entry))
    assert parsed is not None and parsed.title == "worktree: owner resolution needs the main tree"


@pytest.mark.parametrize(
    "value",
    [
        "has a # hash",
        "has: a colon",
        "3 of 6 lost",
        "quotes 'inside' it",
        'double "quotes" too',
        "trailing spaces preserved?  no",
        "unicode — em dash and é",
    ],
)
def test_render_parse_round_trip_with_hazardous_punctuation(value):
    """Property: any field content survives render -> parse unchanged."""
    entry = _entry(title=value, anchor=value, author=value.replace(" ", "-"))
    parsed = parse_entry(render_entry(entry))
    assert parsed is not None
    assert parsed.title == value
    assert parsed.anchor == value


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


# ---------------------------------------------------------------------------
# knowledge-v2: state + function schema


def test_default_state_is_active():
    assert _entry().state == STATE_ACTIVE
    assert validate_entry(_entry()) == []


def test_unknown_state_is_refused():
    assert any("state must be one of" in e for e in validate_entry(_entry(state="archived")))


def test_all_states_validate():
    for st in STATES:
        assert validate_entry(_entry(state=st)) == [], st


def test_function_enum_is_romans_eight_fixed_values():
    # spec-agent ruling 20260924T223749 (Roman's taxonomy, otaman-specs PR #484)
    assert FUNCTIONS == (
        "marketing",
        "sales",
        "research",
        "analysis",
        "design",
        "development",
        "quality",
        "support",
    )


def test_function_value_checked_when_set_empty_tolerated_as_legacy():
    # empty function = a legacy/unassigned entry: read, not rejected (the
    # blocked_entries.kind precedent; the six v1 entries + gate 4.1 depend on it)
    assert validate_entry(_entry(function="")) == []
    # a non-empty value must be one of the fixed 8
    assert any("function, when set" in e for e in validate_entry(_entry(function="strategy")))
    for fn in FUNCTIONS:
        assert validate_entry(_entry(function=fn)) == [], fn


def test_a_legacy_v1_entry_is_valid_to_operate_on():
    """A shared-agent-memory (v1) entry has no function; it must validate so it can
    be listed, shown, and AMENDED (gate 4.1 amends exactly such an entry) —
    'valid to read, invalid to write' with no path between is the anti-pattern."""
    v1 = (
        "---\ntype: lesson\nauthor: plugin-agent\ncreated: 2026-09-23\n"
        "review-by: 2026-12-23\nanchor: 20260923T191139-x\n"
        "title: macos support is held\n---\nbody\n"
    )
    parsed = parse_entry(v1)
    assert parsed is not None and parsed.function == ""
    assert validate_entry(parsed) == []


def test_v2_fields_round_trip():
    entry = _entry(
        state=STATE_DORMANT,
        function=FUNCTION_RESEARCH,
        domain="gtm",
        supersedes="2026-09-01-old-take",
        accessed_at="2026-09-24",
    )
    assert parse_entry(render_entry(entry)) == entry


def test_optional_v2_fields_absent_round_trip_as_defaults():
    # an entry with no function/domain/supersedes/accessed-at parses back with ""
    entry = _entry(function="")  # invalid to validate, but must still render/parse
    parsed = parse_entry(render_entry(entry))
    assert parsed is not None
    assert parsed.function == "" and parsed.domain == "" and parsed.supersedes == ""
    assert parsed.accessed_at == "" and parsed.state == STATE_ACTIVE


def test_v1_entry_still_parses_with_defaults():
    # a shared-agent-memory (v1) entry has no state/function fields
    v1 = (
        "---\ntype: fact\nauthor: a\ncreated: 2026-09-01\n"
        "review-by: 2026-12-01\nanchor: x:1\ntitle: t\n---\nbody\n"
    )
    parsed = parse_entry(v1)
    assert parsed is not None
    assert parsed.state == STATE_ACTIVE and parsed.function == ""


# ---------------------------------------------------------------------------
# knowledge-v2: the amend edge


def test_amend_writes_the_correcting_entry_with_a_supersedes_pointer(tmp_path):
    kd = tmp_path / "knowledge"
    old = write_entry(kd, _entry(title="macos needs X", created="2026-09-01"))
    new = _entry(title="macos needs Y not X", created="2026-09-23", anchor="20260923T191139-x")
    old_stem = "2026-09-01-macos-needs-x"
    path = amend_entry(kd, old_stem, new)
    written = load_entry_by_stem(kd, path.stem)
    assert written is not None and written.supersedes == old_stem
    # the original bytes are unchanged (never rewritten)
    assert old.read_text(encoding="utf-8") == render_entry(
        _entry(title="macos needs X", created="2026-09-01")
    )


def test_amend_to_a_missing_entry_is_refused(tmp_path):
    kd = tmp_path / "knowledge"
    kd.mkdir()
    with pytest.raises(KnowledgeError, match="no such entry"):
        amend_entry(kd, "does-not-exist", _entry())


def test_amended_original_is_derived_both_directions(tmp_path):
    kd = tmp_path / "knowledge"
    write_entry(kd, _entry(title="old", created="2026-09-01"))
    amend_entry(kd, "2026-09-01-old", _entry(title="new", created="2026-09-23", anchor="x:2"))
    entries = load_entries(kd)
    old = next(e for e in entries if e.title == "old")
    new = next(e for e in entries if e.title == "new")
    # forward edge on the corrector; reverse edge derived on the original
    assert new.supersedes == "2026-09-01-old"
    assert is_amended(old, entries) is True
    assert is_amended(new, entries) is False
    assert superseded_by(entries, "2026-09-01-old") == [new.stem]


# ---------------------------------------------------------------------------
# knowledge-v2: lifecycle mutations


def test_mark_accessed_bumps_accessed_at(tmp_path):
    kd = tmp_path / "knowledge"
    write_entry(kd, _entry())
    stem = _entry().stem
    mark_accessed(kd, stem, "2026-09-24")
    assert load_entry_by_stem(kd, stem).accessed_at == "2026-09-24"


def test_mark_accessed_missing_entry_returns_none(tmp_path):
    assert mark_accessed(tmp_path / "knowledge", "nope", "2026-09-24") is None


def test_set_state_moves_to_dormant_reversibly(tmp_path):
    kd = tmp_path / "knowledge"
    write_entry(kd, _entry())
    stem = _entry().stem
    set_state(kd, stem, STATE_DORMANT)
    assert load_entry_by_stem(kd, stem).state == STATE_DORMANT
    set_state(kd, stem, STATE_ACTIVE)  # reversible
    assert load_entry_by_stem(kd, stem).state == STATE_ACTIVE


def test_set_state_rejects_unknown_state(tmp_path):
    kd = tmp_path / "knowledge"
    write_entry(kd, _entry())
    with pytest.raises(KnowledgeError, match="unknown state"):
        set_state(kd, _entry().stem, "archived")


# ---------------------------------------------------------------------------
# knowledge-v2: the index


def test_active_entries_excludes_dormant_and_retired():
    entries = [
        _entry(title="a"),
        _entry(title="b", state=STATE_DORMANT),
        _entry(title="c", state="retired"),
    ]
    assert [e.title for e in active_entries(entries)] == ["a"]


def test_in_partition_scopes_by_function():
    entries = [
        _entry(title="d", function=FUNCTION_DEVELOPMENT),
        _entry(title="s", function=FUNCTION_RESEARCH),
    ]
    assert [e.title for e in in_partition(entries, FUNCTION_RESEARCH)] == ["s"]


def test_index_line_carries_id_type_state_description():
    line = index_line(_entry(title="a lesson"))
    assert _entry(title="a lesson").stem in line
    assert "[lesson/active]" in line
    assert "a lesson" in line
    assert AMENDED_FLAG not in line


def test_index_line_amended_flag():
    assert AMENDED_FLAG in index_line(_entry(), amended=True)
