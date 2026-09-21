"""shared-logic-single-home 1.2 — the one typed bus-frontmatter parser, in core.

Seven parsers were scattered across cli and plugin and disagreed on types:
plugin's regex line-splitter returned every value as a string (so cc was the
string "[spec-agent]" and x-cc the string "true", needing a bespoke
_parse_cc_field to recover them), while cli used real YAML and got a list and a
bool. These pin the single typed contract: cc is a list, x-cc is a bool, every
other field is a scalar, and the two protocol fields have canonical readers so a
message written by either transport parses identically.
"""

from __future__ import annotations

from otaman_core.frontmatter import cc_recipients, is_cc_copy, parse

MESSAGE = """---
id: 20260921T101010-runner-a
from: runner-agent
to: human
cc: [spec-agent, cpo-agent]
priority: normal
type: outcome-proposal
---
## Subject: a proposal

first body line
second body line
"""


# ---------------------------------------------------------------------------
# splitting + scalar typing


def test_parse_splits_frontmatter_and_body():
    fm, body = parse(MESSAGE)
    assert fm["from"] == "runner-agent"
    assert fm["to"] == "human"
    assert body.startswith("## Subject: a proposal")
    assert "second body line" in body


def test_scalar_fields_are_typed_by_yaml():
    fm, _ = parse(MESSAGE)
    assert isinstance(fm["from"], str)
    assert isinstance(fm["type"], str)
    assert fm["priority"] == "normal"


def test_no_frontmatter_returns_empty_dict_and_full_text():
    fm, body = parse("just some text\nno fence here\n")
    assert fm == {}
    assert body == "just some text\nno fence here\n"


def test_a_file_without_a_closing_fence_is_not_frontmatter():
    fm, _ = parse("---\nid: x\nno closing fence\n")
    assert fm == {}


def test_malformed_yaml_frontmatter_is_not_raised():
    fm, _ = parse("---\n: : : not yaml : :\n---\nbody\n")
    assert fm == {}


def test_crlf_line_endings_are_tolerated():
    fm, body = parse("---\r\nfrom: a\r\nto: b\r\n---\r\nbody\r\n")
    assert fm["from"] == "a" and fm["to"] == "b"
    assert "body" in body


def test_empty_body_is_fine():
    fm, body = parse("---\nfrom: a\n---\n")
    assert fm["from"] == "a"
    assert body == ""


# ---------------------------------------------------------------------------
# cc is a LIST (bus-message-cc-field), not a string


def test_cc_inline_list_is_a_list():
    fm, _ = parse(MESSAGE)
    assert isinstance(fm["cc"], list)
    assert cc_recipients(fm) == ["spec-agent", "cpo-agent"]


def test_cc_block_list_is_a_list():
    fm, _ = parse("---\nto: human\ncc:\n  - spec-agent\n  - cpo-agent\n---\nb\n")
    assert cc_recipients(fm) == ["spec-agent", "cpo-agent"]


def test_cc_quoted_names_are_a_list():
    fm, _ = parse('---\ncc: ["a", "b"]\n---\nb\n')
    assert cc_recipients(fm) == ["a", "b"]


def test_cc_scalar_becomes_a_single_item_list():
    fm, _ = parse("---\ncc: spec-agent\n---\nb\n")
    assert cc_recipients(fm) == ["spec-agent"]


def test_cc_absent_is_empty():
    fm, _ = parse("---\nto: human\n---\nb\n")
    assert cc_recipients(fm) == []


def test_cc_blank_is_empty():
    fm, _ = parse("---\ncc:\n---\nb\n")
    assert cc_recipients(fm) == []


def test_cc_drops_blank_entries():
    fm, _ = parse('---\ncc: ["a", "", "  ", "b"]\n---\nb\n')
    assert cc_recipients(fm) == ["a", "b"]


# ---------------------------------------------------------------------------
# x-cc is a BOOL — the CC-copy marker (bus-message-cc-field)


def test_x_cc_true_bool_is_a_cc_copy():
    fm, _ = parse("---\nto: spec-agent\nx-cc: true\n---\nb\n")
    assert fm["x-cc"] is True
    assert is_cc_copy(fm) is True


def test_x_cc_string_true_is_also_a_cc_copy():
    """A type-losing parser (or a hand-edited file) may leave the marker as the
    string 'true'; a copy written by either transport must read the same."""
    fm, _ = parse('---\nto: spec-agent\nx-cc: "true"\n---\nb\n')
    assert is_cc_copy(fm) is True


def test_primary_message_without_x_cc_is_not_a_copy():
    fm, _ = parse(MESSAGE)
    assert is_cc_copy(fm) is False


def test_x_cc_false_is_not_a_copy():
    fm, _ = parse("---\nx-cc: false\n---\nb\n")
    assert is_cc_copy(fm) is False


# ---------------------------------------------------------------------------
# the spec scenario: transports cannot disagree


def test_transports_return_the_same_typed_result():
    """One artifact, one parser, one typed result — cc a list, x-cc a bool, no
    matter which surface reads it (the divergence this change removes)."""
    delivered_cc_copy = """---
id: 20260921T101010-runner-a
from: runner-agent
to: spec-agent
cc: [spec-agent]
x-cc: true
type: outcome-proposal
---
## Subject: a proposal

body
"""
    fm, _ = parse(delivered_cc_copy)
    assert isinstance(fm["cc"], list)
    assert isinstance(fm["x-cc"], bool)
    assert cc_recipients(fm) == ["spec-agent"]
    assert is_cc_copy(fm) is True
