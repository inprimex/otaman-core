"""shared-logic-single-home 1.1 — the one blocked-entry parser/writer, in core.

Entries were parsed by ad-hoc regexes in five places, each with its own idea of
what an entry is; an entry visible in one transport was invisible in another.
This module is the single home. These pin the parsing/matching/writing contract
plus the malformed-visibility ruling (D2): the well-formed shape is
``## Blocked: `` (space + non-empty title), the bare regex is only the detection
floor, and an entry matching the floor but not the form surfaces as
``[malformed]`` on every surface and stays terminable.

The CLI-/plugin-transport wiring tests (``_clear_dependency_waits`` and the
subcommands) stay in their own repos against this shared module.
"""

from __future__ import annotations

from otaman_core.blocked_entries import (
    KIND_APPROVAL,
    KIND_DEPENDENCY,
    MALFORMED_TITLE,
    find_by_ref,
    parse_entries,
    render_entry,
    tombstone,
)

APPROVAL_ENTRY = """## Blocked: blocked-entry lifecycle: what terminates an entry
- **Proposal**: 20260913T144246-cli-agent-to-human-spec-change-request
- **Blocked since**: 2026-09-13T14:42:46Z
- **Depends on**: spec-change-approved + spec-change notification
"""

DEPENDENCY_ENTRY = """## Blocked: waiting on the policy engine
- **Change**: policy-engine
- **Kind**: awaiting-dependency
- **Blocked since**: 2026-09-01T00:00:00Z
"""


# ---------------------------------------------------------------------------
# parsing


def test_parses_title_and_fields():
    (entry,) = parse_entries(APPROVAL_ENTRY)
    assert entry.title.startswith("blocked-entry lifecycle")
    assert entry.proposal == "20260913T144246-cli-agent-to-human-spec-change-request"
    assert entry.get("blocked since") == "2026-09-13T14:42:46Z"


def test_field_lookup_is_case_insensitive():
    (entry,) = parse_entries(APPROVAL_ENTRY)
    assert entry.get("Proposal") == entry.get("proposal") == entry.proposal


def test_parses_multiple_entries():
    entries = parse_entries(APPROVAL_ENTRY + "\n" + DEPENDENCY_ENTRY)
    assert len(entries) == 2
    assert [e.kind for e in entries] == [KIND_APPROVAL, KIND_DEPENDENCY]


def test_empty_and_garbage_text():
    assert parse_entries("") == []
    assert parse_entries("no entries here\njust prose\n") == []


# ---------------------------------------------------------------------------
# kind inference — legacy entries are READ, not rejected (additive)


def test_declared_kind_wins():
    (entry,) = parse_entries(DEPENDENCY_ENTRY)
    assert entry.kind == KIND_DEPENDENCY


def test_legacy_entry_with_proposal_infers_approval():
    """The live legacy shape: no Kind field, but a Proposal ref and text that
    literally says it waits on human approval."""
    (entry,) = parse_entries(APPROVAL_ENTRY)
    assert "kind" not in entry.fields
    assert entry.kind == KIND_APPROVAL


def test_legacy_entry_without_a_proposal_infers_dependency():
    (entry,) = parse_entries("## Blocked: something\n- **Blocked by**: human\n")
    assert entry.kind == KIND_DEPENDENCY


def test_unknown_kind_value_falls_back_to_inference():
    (entry,) = parse_entries("## Blocked: x\n- **Proposal**: stem-1\n- **Kind**: nonsense\n")
    assert entry.kind == KIND_APPROVAL


# ---------------------------------------------------------------------------
# the stable ref


def test_ref_prefers_proposal():
    (entry,) = parse_entries("## Blocked: x\n- **Proposal**: stem-1\n- **Change**: ch\n")
    assert entry.ref == "stem-1" and entry.has_ref is True


def test_ref_falls_back_to_change():
    (entry,) = parse_entries(DEPENDENCY_ENTRY)
    assert entry.ref == "policy-engine"


def test_entry_without_a_ref():
    (entry,) = parse_entries("## Blocked: refless\n- **Blocked by**: human\n")
    assert entry.ref == "" and entry.has_ref is False


def test_find_by_ref_is_exact_not_substring():
    """A substring match over free text is how an unrelated entry gets cleared by
    someone else's completion — the failure mode this replaces."""
    text = "## Blocked: a\n- **Change**: policy-engine\n\n## Blocked: b\n- **Change**: policy\n"
    assert [e.title for e in find_by_ref(text, "policy")] == ["b"]
    assert [e.title for e in find_by_ref(text, "policy-engine")] == ["a"]


def test_find_by_ref_filters_by_kind():
    text = APPROVAL_ENTRY + "\n" + DEPENDENCY_ENTRY
    assert find_by_ref(text, "policy-engine", kinds=(KIND_APPROVAL,)) == []
    assert len(find_by_ref(text, "policy-engine", kinds=(KIND_DEPENDENCY,))) == 1


def test_find_by_ref_empty_ref_matches_nothing():
    assert find_by_ref(APPROVAL_ENTRY, "") == []
    assert find_by_ref(APPROVAL_ENTRY, "   ") == []


# ---------------------------------------------------------------------------
# tombstones — plugin's format, matched not redefined


def test_tombstone_wraps_and_is_excluded_from_live_reads():
    entries = parse_entries(DEPENDENCY_ENTRY)
    out = tombstone(DEPENDENCY_ENTRY, entries, reason="completed policy-engine", today="2026-09-16")
    assert "<!-- ## Blocked:" in out
    assert "cleared 2026-09-16 — completed policy-engine -->" in out
    assert parse_entries(out) == []  # no longer live


def test_tombstoned_entries_visible_when_asked():
    out = tombstone(
        DEPENDENCY_ENTRY, parse_entries(DEPENDENCY_ENTRY), reason="x", today="2026-09-16"
    )
    (entry,) = parse_entries(out, include_tombstoned=True)
    assert entry.tombstoned is True and entry.cleared_reason == "x"


def test_tombstone_is_idempotent():
    once = tombstone(
        DEPENDENCY_ENTRY, parse_entries(DEPENDENCY_ENTRY), reason="x", today="2026-09-16"
    )
    twice = tombstone(
        once, parse_entries(once, include_tombstoned=True), reason="x", today="2026-09-16"
    )
    assert twice == once


def test_tombstone_leaves_siblings_alone():
    text = APPROVAL_ENTRY + "\n" + DEPENDENCY_ENTRY
    targets = find_by_ref(text, "policy-engine")
    out = tombstone(text, targets, reason="done", today="2026-09-16")
    live = parse_entries(out)
    assert [e.kind for e in live] == [KIND_APPROVAL]  # the approval wait survives


def test_clearing_a_non_last_entry_keeps_later_entries_visible():
    """REGRESSION (plugin-agent repro 20260921T151120): parse_entries captures the
    separator blank line into entry.block; a bare rstrip() in tombstone() ate it,
    so the closing `-->` glued onto the NEXT entry's header — no longer
    line-leading, so parse_entries could not see it. Clearing the FIRST of two
    entries then silently hid the second (and every entry after) — the exact
    "invisible in one transport" failure this module exists to kill. The earlier
    sibling tests only tombstoned the LAST entry, so none caught it."""
    text = (
        "\n## Blocked: first\n- **Proposal**: p\n- **Blocked since**: t\n\n"
        "## Blocked: second\n- **Change**: d\n- **Blocked since**: t\n"
    )
    first = [e for e in parse_entries(text) if e.title == "first"]
    out = tombstone(text, first, reason="r", today="2026-09-21")
    assert [e.title for e in parse_entries(out)] == ["second"]
    # and the cleared entry is still recognisable as tombstoned
    assert [e.title for e in parse_entries(out, include_tombstoned=True)] == ["first", "second"]


# ---------------------------------------------------------------------------
# rendering (additive Kind)


def test_render_includes_kind_and_keeps_existing_fields():
    text = render_entry(
        "my task", kind=KIND_DEPENDENCY, since="2026-09-16T00:00:00Z", extra={"Blocked by": "human"}
    )
    assert text.startswith("## Blocked: my task")
    assert "- **Kind**: awaiting-dependency" in text
    assert "- **Blocked since**: 2026-09-16T00:00:00Z" in text
    assert "- **Blocked by**: human" in text


def test_render_round_trips_through_the_parser():
    text = render_entry("t", kind=KIND_APPROVAL, ref="stem-9", since="2026-09-16T00:00:00Z")
    (entry,) = parse_entries(text)
    assert entry.title == "t" and entry.kind == KIND_APPROVAL and entry.ref == "stem-9"


def test_render_defaults_kind_from_the_ref_label():
    assert "awaiting-approval" in render_entry("t", ref="s", ref_label="Proposal")
    assert "awaiting-dependency" in render_entry("t", ref="c", ref_label="Change")


def test_rendered_entries_are_well_formed():
    """The writer never emits a malformed entry — space + non-empty title."""
    (entry,) = parse_entries(render_entry("a real title", ref="stem-1"))
    assert entry.malformed is False
    assert entry.display_title == "a real title"


# ---------------------------------------------------------------------------
# malformed visibility (shared-logic-single-home, D2) — the founding defect


def test_empty_title_entry_is_flagged_malformed_not_dropped():
    """`## Blocked:` with no title matched the detection floor but not the form.
    It must appear — flagged — on every surface, never silently vanish."""
    (entry,) = parse_entries("## Blocked:\n- **Change**: policy-engine\n")
    assert entry.malformed is True
    assert entry.display_title == MALFORMED_TITLE
    assert entry.title == ""


def test_space_only_title_is_malformed():
    (entry,) = parse_entries("## Blocked: \n- **Change**: c\n")
    assert entry.malformed is True
    assert entry.display_title == MALFORMED_TITLE


def test_missing_space_after_colon_is_malformed():
    """The reference hook's test is `## Blocked: ` with the trailing space
    (check-blocked.sh:96, `'## Blocked: '*`); no space is not the form."""
    (entry,) = parse_entries("## Blocked:no-space-title\n- **Change**: c\n")
    assert entry.malformed is True


def test_a_well_formed_entry_is_not_malformed():
    (entry,) = parse_entries(APPROVAL_ENTRY)
    assert entry.malformed is False
    assert entry.display_title == entry.title


def test_malformed_entry_is_still_terminable():
    """No entry may become permanently un-tombstonable — the malformed one is
    matched by its captured block like any other and clears cleanly."""
    text = "## Blocked:\n- **Change**: policy-engine\n"
    (entry,) = parse_entries(text)
    out = tombstone(text, [entry], reason="removed bad entry", today="2026-09-21")
    assert "<!-- ## Blocked:" in out
    assert parse_entries(out) == []  # cleared from the live set


def test_malformed_entry_does_not_hide_a_well_formed_sibling():
    text = "## Blocked:\n- **Change**: c\n\n" + DEPENDENCY_ENTRY
    entries = parse_entries(text)
    assert len(entries) == 2
    assert [e.malformed for e in entries] == [True, False]
    assert [e.display_title for e in entries] == [MALFORMED_TITLE, "waiting on the policy engine"]
