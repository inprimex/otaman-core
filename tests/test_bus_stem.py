"""shared-logic-single-home 1.3 — the one bus filename/stem writer/parser pair.

Four sites hand-built `<ts>-<sender>-to-<recipient>-<slug>` and several
hand-parsed it with divergent timestamp regexes and slugifiers. These pin the
single convention: build_stem owns the `-to-` route format, slugify is shared,
and parse_stem reads it back — resolving the ambiguous recipient/slug boundary
only when handed the known recipients, never by guessing.
"""

from __future__ import annotations

from otaman_core.bus_stem import (
    build_filename,
    build_stem,
    parse_stem,
    slugify,
    timestamp_of,
)

AGENTS = {"human", "all", "core-agent", "cli-agent", "spec-agent", "core"}


# ---------------------------------------------------------------------------
# slugify


def test_slugify_basic():
    assert slugify("Host scr_template in otaman-core") == "host-scr-template-in-otaman-core"


def test_slugify_collapses_and_trims():
    assert slugify("  A --  B!! ") == "a-b"


def test_slugify_max_len_truncates_and_retrims():
    # cut lands on a hyphen boundary; the trailing hyphen is trimmed
    assert slugify("aaaa-bbbb-cccc", max_len=9) == "aaaa-bbbb"


def test_slugify_empty_uses_fallback():
    assert slugify("") == "item"
    assert slugify("!!!") == "item"
    assert slugify("", fallback="x") == "x"


# ---------------------------------------------------------------------------
# build_stem / build_filename


def test_build_stem_format():
    stem = build_stem(
        timestamp="20260921T150141", sender="core-agent", recipient="cli-agent", slug="a-thing"
    )
    assert stem == "20260921T150141-core-agent-to-cli-agent-a-thing"


def test_build_filename_adds_md():
    fn = build_filename(timestamp="20260921T150141", sender="human", recipient="all", slug="hi")
    assert fn == "20260921T150141-human-to-all-hi.md"


def test_build_stem_neutralizes_path_separator():
    stem = build_stem(timestamp="20260921T150141", sender="a", recipient="org/team", slug="s")
    assert "/" not in stem
    assert stem == "20260921T150141-a-to-org-team-s"


# ---------------------------------------------------------------------------
# timestamp_of


def test_timestamp_of():
    assert timestamp_of("20260921T150141-human-to-all-spec-change-approved") == "20260921T150141"
    assert timestamp_of("20260921T150141-x.md") == "20260921T150141"
    assert timestamp_of("not-a-stem") == ""


def test_timestamp_of_ignores_a_same_second_counter():
    # a stem whose time token carries a 2-digit counter still yields the compact form
    assert timestamp_of("20260426T15164601-maestro-to-backend-agent-x") == "20260426T151646"


# ---------------------------------------------------------------------------
# parse_stem — timestamp + sender always; recipient/slug need the agent set


def test_parse_recovers_timestamp_and_sender_without_agents():
    p = parse_stem("20260921T150141-human-to-all-spec-change-approved")
    assert p is not None
    assert p.timestamp == "20260921T150141"
    assert p.sender == "human"
    assert p.tail == "all-spec-change-approved"
    # ambiguous without the agent set — not guessed
    assert p.recipient is None and p.slug is None


def test_parse_resolves_recipient_and_slug_with_agents():
    p = parse_stem(
        "20260920T112300-cli-agent-to-core-agent-request-host-scr-template",
        known_recipients=AGENTS,
    )
    assert p is not None
    assert p.sender == "cli-agent"
    assert p.recipient == "core-agent"
    assert p.slug == "request-host-scr-template"


def test_parse_prefers_the_longest_recipient_match():
    # 'core' is also a known name, but 'core-agent' is the longer prefix and wins
    p = parse_stem("20260921T101010-human-to-core-agent-nudge-x", known_recipients=AGENTS)
    assert p is not None and p.recipient == "core-agent" and p.slug == "nudge-x"


def test_parse_broadcast_to_all():
    p = parse_stem("20260921T150141-human-to-all-spec-change-approved", known_recipients=AGENTS)
    assert p is not None and p.recipient == "all" and p.slug == "spec-change-approved"


def test_parse_strips_md_extension():
    p = parse_stem("20260921T150141-a-to-human-x.md", known_recipients=AGENTS)
    assert p is not None and p.recipient == "human" and p.slug == "x"


def test_parse_handles_a_recipient_with_no_slug():
    p = parse_stem("20260921T150141-a-to-human", known_recipients=AGENTS)
    assert p is not None and p.recipient == "human" and p.slug is None


def test_parse_no_route_yields_no_recipient():
    p = parse_stem("20260921T150141-some-non-message-file")
    assert p is not None
    assert p.timestamp == "20260921T150141"
    assert p.recipient is None and p.slug is None and p.sender == ""


def test_parse_returns_none_without_a_timestamp():
    assert parse_stem("just-a-name") is None
    assert parse_stem("agents.yaml") is None


def test_parse_unknown_recipient_stays_ambiguous():
    # the agent set does not contain the recipient → not guessed
    p = parse_stem("20260921T150141-a-to-someone-else-x", known_recipients={"human", "all"})
    assert p is not None and p.recipient is None and p.slug is None


# ---------------------------------------------------------------------------
# round-trip: build then parse recovers the components


def test_round_trip_with_hyphenated_names():
    stem = build_stem(
        timestamp="20260921T150141",
        sender="cli-agent",
        recipient="core-agent",
        slug=slugify("Request: host scr_template"),
    )
    p = parse_stem(stem, known_recipients=AGENTS)
    assert p is not None
    assert p.timestamp == "20260921T150141"
    assert p.sender == "cli-agent"
    assert p.recipient == "core-agent"
    assert p.slug == "request-host-scr-template"


def test_round_trip_composite_slug():
    # the console-decision shape: recipient 'all', slug 'console-<verb>-<subject>'
    slug = f"console-approve-{slugify('a proposal', max_len=30)}"
    stem = build_stem(timestamp="20260921T150141", sender="human", recipient="all", slug=slug)
    p = parse_stem(stem, known_recipients=AGENTS)
    assert p is not None and p.recipient == "all" and p.slug == "console-approve-a-proposal"
