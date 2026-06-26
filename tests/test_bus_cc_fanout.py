"""Tests for otaman_core.bus.cc_fanout — shared CC routing primitives.

Covers cli-send-cc-fanout-parity task 2.1. Semantics preserved bit-for-bit
from otaman-plugin's bus_server.py (PR #50 + PR #52 + PR #80). Tests
mirror otaman-plugin/tests/test_bus_cc_routing.py + test_outcome_proposal_
routing.py so both consumers get the same invariants asserted by the
otaman-core suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_core.bus.cc_fanout import (
    compute_effective_cc,
    evaluate_routing_rules,
    inject_x_cc,
    load_routing_rules,
)


# ---------------------------------------------------------------------------
# load_routing_rules


class TestLoadRoutingRules:
    def test_missing_platform_yaml_returns_empty(self, tmp_path: Path):
        assert load_routing_rules(tmp_path) == []

    def test_no_bus_section_returns_empty(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "project: example\nversion: '1.0'\nrepos: []\n",
            encoding="utf-8",
        )
        assert load_routing_rules(tmp_path) == []

    def test_no_routing_rules_returns_empty(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "bus:\n  transport: file\n",
            encoding="utf-8",
        )
        assert load_routing_rules(tmp_path) == []

    def test_rules_load_as_dicts(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "bus:\n"
            "  routing_rules:\n"
            "    - when: { to: human }\n"
            "      cc: [spec-agent]\n",
            encoding="utf-8",
        )
        rules = load_routing_rules(tmp_path)
        assert len(rules) == 1
        assert rules[0]["when"] == {"to": "human"}
        assert rules[0]["cc"] == ["spec-agent"]

    def test_malformed_yaml_returns_empty(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "not: valid: yaml: [",
            encoding="utf-8",
        )
        assert load_routing_rules(tmp_path) == []

    def test_routing_rules_not_list_returns_empty(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "bus:\n  routing_rules: not-a-list\n",
            encoding="utf-8",
        )
        assert load_routing_rules(tmp_path) == []

    def test_non_dict_items_filtered_out(self, tmp_path: Path):
        (tmp_path / "platform.yaml").write_text(
            "bus:\n"
            "  routing_rules:\n"
            "    - { when: { to: human }, cc: [spec-agent] }\n"
            "    - just-a-string\n",
            encoding="utf-8",
        )
        rules = load_routing_rules(tmp_path)
        assert len(rules) == 1


# ---------------------------------------------------------------------------
# evaluate_routing_rules


class TestEvaluateRoutingRules:
    def test_no_rules_no_cc(self):
        assert evaluate_routing_rules([], "human", "normal") == set()

    def test_to_match_fires(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        assert evaluate_routing_rules(rules, "human", "normal") == {"spec-agent"}

    def test_to_mismatch_skips(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        assert evaluate_routing_rules(rules, "core-agent", "normal") == set()

    def test_priority_scalar_match(self):
        rules = [{"when": {"to": "human", "priority": "high"}, "cc": ["x"]}]
        assert evaluate_routing_rules(rules, "human", "high") == {"x"}
        assert evaluate_routing_rules(rules, "human", "normal") == set()

    def test_priority_list_or_semantics(self):
        rules = [{"when": {"to": "human", "priority": ["high", "urgent"]}, "cc": ["x"]}]
        assert evaluate_routing_rules(rules, "human", "high") == {"x"}
        assert evaluate_routing_rules(rules, "human", "urgent") == {"x"}
        assert evaluate_routing_rules(rules, "human", "normal") == set()

    def test_type_aware_match(self):
        rules = [{"when": {"type": "outcome-proposal"}, "cc": ["cofounder-agent"]}]
        assert evaluate_routing_rules(
            rules, "human", "normal", msg_type="outcome-proposal",
        ) == {"cofounder-agent"}

    def test_type_with_none_msg_type_skips(self):
        """when.type set but caller passes None — rule MUST NOT match."""
        rules = [{"when": {"type": "outcome-proposal"}, "cc": ["x"]}]
        assert evaluate_routing_rules(rules, "human", "normal", None) == set()

    def test_type_list_or_semantics(self):
        rules = [{"when": {"type": ["outcome-proposal", "review-request"]}, "cc": ["x"]}]
        assert evaluate_routing_rules(
            rules, "human", "normal", "outcome-proposal",
        ) == {"x"}
        assert evaluate_routing_rules(
            rules, "human", "normal", "info",
        ) == set()

    def test_union_across_multiple_matching_rules(self):
        rules = [
            {"when": {"to": "human"}, "cc": ["spec-agent"]},
            {"when": {"to": "human", "priority": "high"}, "cc": ["cofounder-agent"]},
        ]
        # Both fire on high-priority human messages.
        assert evaluate_routing_rules(rules, "human", "high") == {
            "spec-agent", "cofounder-agent",
        }

    def test_unknown_when_key_skips_rule(self):
        """Forward-compat: unknown keys in when: drop the rule silently."""
        rules = [
            {"when": {"to": "human", "future_key": "foo"}, "cc": ["x"]},
            {"when": {"to": "human"}, "cc": ["y"]},
        ]
        assert evaluate_routing_rules(rules, "human", "normal") == {"y"}

    def test_non_dict_when_skipped(self):
        rules = [{"when": "not-a-dict", "cc": ["x"]}]
        assert evaluate_routing_rules(rules, "human", "normal") == set()

    def test_non_list_cc_skipped(self):
        rules = [{"when": {"to": "human"}, "cc": "not-a-list"}]
        assert evaluate_routing_rules(rules, "human", "normal") == set()

    def test_non_string_cc_entries_filtered(self):
        rules = [{"when": {"to": "human"}, "cc": ["good", 42, "", "also-good"]}]
        assert evaluate_routing_rules(rules, "human", "normal") == {"good", "also-good"}


# ---------------------------------------------------------------------------
# compute_effective_cc


class TestComputeEffectiveCc:
    def test_no_explicit_no_rules(self):
        assert compute_effective_cc("human", "normal", None, []) == []

    def test_explicit_only(self):
        assert compute_effective_cc(
            "human", "normal", ["plugin-agent"], [],
        ) == ["plugin-agent"]

    def test_routing_rule_only(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        assert compute_effective_cc("human", "normal", None, rules) == ["spec-agent"]

    def test_union_explicit_plus_rule(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        result = compute_effective_cc("human", "normal", ["plugin-agent"], rules)
        assert set(result) == {"plugin-agent", "spec-agent"}

    def test_dedup_when_explicit_and_rule_agree(self):
        rules = [{"when": {"to": "human"}, "cc": ["spec-agent"]}]
        result = compute_effective_cc("human", "normal", ["spec-agent"], rules)
        assert result == ["spec-agent"]

    def test_primary_to_excluded(self):
        """If a rule names the primary recipient, drop them from CC."""
        rules = [{"when": {"to": "human"}, "cc": ["human", "spec-agent"]}]
        result = compute_effective_cc("human", "normal", None, rules)
        assert "human" not in result
        assert result == ["spec-agent"]

    def test_explicit_first_then_rules_sorted(self):
        """Order: explicit insertion order, then rule-derived sorted."""
        rules = [{"when": {"to": "human"}, "cc": ["z-agent", "a-agent"]}]
        result = compute_effective_cc("human", "normal", ["plugin-agent"], rules)
        assert result == ["plugin-agent", "a-agent", "z-agent"]

    def test_type_aware_rule(self):
        rules = [{"when": {"type": "outcome-proposal"}, "cc": ["cofounder-agent"]}]
        assert compute_effective_cc(
            "human", "normal", None, rules, msg_type="outcome-proposal",
        ) == ["cofounder-agent"]

    def test_non_string_explicit_cc_filtered(self):
        assert compute_effective_cc(
            "human", "normal", ["good", 42, "", None], [],  # type: ignore[list-item]
        ) == ["good"]


# ---------------------------------------------------------------------------
# inject_x_cc


class TestInjectXCc:
    def test_appends_after_last_field(self):
        content = (
            "---\n"
            "id: 1\n"
            "to: human\n"
            "---\n"
            "\n## Subject: test\n"
        )
        out = inject_x_cc(content)
        assert "x-cc: true" in out
        # x-cc is the last frontmatter field, immediately before closing ---
        idx_xcc = out.index("x-cc: true")
        idx_close = out.index("\n---", idx_xcc)
        assert out[idx_xcc:idx_close].strip() == "x-cc: true"

    def test_preserves_body(self):
        body = "\n## Subject: test\n\nBody content here.\n"
        content = f"---\nid: 1\nto: human\n---{body}"
        out = inject_x_cc(content)
        assert body in out

    def test_lowercase_value(self):
        """x-cc value must be lowercase 'true', no quote-wrapping —
        downstream filters expect this byte-exact form."""
        content = "---\nid: 1\nto: human\n---\n"
        out = inject_x_cc(content)
        assert "x-cc: true" in out
        assert "x-cc: True" not in out
        assert 'x-cc: "true"' not in out

    def test_malformed_frontmatter_unchanged(self):
        """No frontmatter delimiters at all — return unchanged."""
        content = "no frontmatter here\nx-cc would be wrong here\n"
        assert inject_x_cc(content) == content

    def test_idempotent_appearance(self):
        """Calling twice doesn't double the x-cc line — the regex only
        matches the first ---...--- block, and the new line itself is
        a valid frontmatter field, so re-injection adds another copy.
        This documents the current behavior; callers must not re-inject."""
        content = "---\nid: 1\nto: human\n---\n"
        once = inject_x_cc(content)
        twice = inject_x_cc(once)
        assert twice.count("x-cc: true") == 2


# ---------------------------------------------------------------------------
# Integration — end-to-end fan-out semantics


class TestFanOutIntegration:
    def test_full_pipeline_with_explicit_and_rule_cc(self, tmp_path: Path):
        """Realistic flow: load rules → compute CC → inject x-cc."""
        (tmp_path / "platform.yaml").write_text(
            "bus:\n"
            "  routing_rules:\n"
            "    - when: { to: human }\n"
            "      cc: [spec-agent]\n",
            encoding="utf-8",
        )
        rules = load_routing_rules(tmp_path)
        cc = compute_effective_cc(
            "human", "high", ["plugin-agent"], rules, msg_type="info",
        )
        assert set(cc) == {"plugin-agent", "spec-agent"}

        # Per-recipient copy
        original = (
            "---\nid: 1\nfrom: core-agent\nto: human\n"
            "cc: [plugin-agent, spec-agent]\n"
            "---\n\n## Subject: hello\n"
        )
        for recipient in cc:
            stamped = inject_x_cc(original)
            assert "x-cc: true" in stamped
            # Primary content preserved verbatim outside frontmatter
            assert "## Subject: hello" in stamped

    def test_type_aware_routing_with_outcome_proposal(self, tmp_path: Path):
        """outcome-proposal-routing flow: type-only rule fires for the type,
        not for sibling types."""
        (tmp_path / "platform.yaml").write_text(
            "bus:\n"
            "  routing_rules:\n"
            "    - when: { type: outcome-proposal }\n"
            "      cc: [cofounder-agent]\n",
            encoding="utf-8",
        )
        rules = load_routing_rules(tmp_path)
        # Fires
        assert compute_effective_cc(
            "human", "normal", None, rules, msg_type="outcome-proposal",
        ) == ["cofounder-agent"]
        # Doesn't fire — different type
        assert compute_effective_cc(
            "human", "normal", None, rules, msg_type="info",
        ) == []
