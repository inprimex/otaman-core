#!/usr/bin/env python3
"""cc-fan-out — shared CC routing logic for bus message senders.

Both the otaman-plugin MCP server (``otaman_plugin.servers.bus_server``) and
the otaman-cli bash command (``otaman_cli.main.cmd_send``) need to compute
the effective CC list for an outgoing bus message and stamp ``x-cc: true``
on each per-recipient copy. Until this module existed, only the MCP path
implemented fan-out — the bash CLI wrote the ``cc:`` field but never
materialised per-recipient files. The asymmetry caused at least one
production incident (cli-send-cc-fanout-parity, 2026-06-22) where a CC'd
recipient missed a critical message because their ``otaman check`` filter
correctly dropped the primary copy (no ``x-cc:`` flag) and no per-recipient
copy existed.

The lift here is intentionally minimal: only the four pure-logic helpers
move (rule loading, rule evaluation, effective-CC composition, frontmatter
stamping). The per-file write step stays in each transport because that's
where the bus-directory layout, atomic-write strategy, and filename
conventions live.

Semantics preserved bit-for-bit from ``bus_server.py`` (PR #50 + PR #52 +
PR #80):

- Routing rules union (not first-match-wins); rules with unknown ``when``
  keys are skipped silently for forward-compatibility.
- ``when.priority`` and ``when.type`` support both scalar and list forms
  (the list form is OR semantics across the values).
- ``compute_effective_cc`` deduplicates while preserving insertion order
  (explicit ``--cc`` first, then routing-rule-derived in sorted order) so
  on-disk message files are deterministic for diff-based assertions.
- The primary ``to:`` recipient is excluded from the effective CC even if
  a rule names them.
- ``inject_x_cc`` appends ``x-cc: true`` as the LAST frontmatter line
  before the closing ``---``. Lowercase, no quote-wrapping; downstream
  filters in ``otaman check`` read this verbatim.

Consumers MUST import from this module rather than re-implementing the
logic. New ``when`` keys and CC selection rules land here once and both
transports pick them up automatically.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def load_routing_rules(root: Path) -> list[dict[str, Any]]:
    """Load ``bus.routing_rules`` from ``platform.yaml`` at the project root.

    Returns an empty list when the file is missing, malformed, or contains
    no ``bus.routing_rules`` section. Pure YAML parse — schema validation
    happens at rule evaluation time. ``yaml`` is imported lazily so this
    module stays importable without a PyYAML dependency at top level.
    """
    platform = root / "platform.yaml"
    if not platform.is_file():
        return []
    try:
        import yaml  # local import keeps the module load lightweight
    except ImportError:
        return []
    try:
        data = yaml.safe_load(platform.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return []
    bus_cfg = data.get("bus") or {}
    rules = bus_cfg.get("routing_rules") or []
    if not isinstance(rules, list):
        return []
    return [r for r in rules if isinstance(r, dict)]


def evaluate_routing_rules(
    rules: list[dict[str, Any]],
    to: str,
    priority: str,
    msg_type: str | None = None,
) -> set[str]:
    """Return the union of ``cc:`` lists from all rules that match.

    Per bus-cc-routing design Q3: rules are evaluated in order, but all
    matching rules contribute (union, not first-match-wins). A rule matches
    when every ``when.<field>`` constraint is satisfied (AND semantics):

    - ``when.to: <name>`` requires exact string equality with ``to``.
    - ``when.priority: <val>`` matches when ``priority`` equals the single
      value, or appears in a list (OR semantics for the list form).
    - ``when.type: <val>`` (outcome-proposal-routing 1.1) matches when
      ``msg_type`` equals the single value, or appears in a list. A rule
      with ``when.type`` set never matches when the caller passes
      ``msg_type=None`` (the caller can't claim AND-matches on a field it
      didn't specify).

    Unknown ``when`` keys cause the rule to be skipped silently — keeps the
    evaluator forward-compatible with future ``when`` extensions without
    breaking older bus servers.
    """
    cc_union: set[str] = set()
    supported_when_keys = {"to", "priority", "type"}
    for rule in rules:
        when = rule.get("when") or {}
        if not isinstance(when, dict):
            continue
        if not set(when.keys()).issubset(supported_when_keys):
            continue
        if "to" in when and when["to"] != to:
            continue
        if "priority" in when:
            pri = when["priority"]
            if isinstance(pri, list):
                if priority not in pri:
                    continue
            elif pri != priority:
                continue
        if "type" in when:
            if msg_type is None:
                continue
            typ = when["type"]
            if isinstance(typ, list):
                if msg_type not in typ:
                    continue
            elif typ != msg_type:
                continue
        cc_list = rule.get("cc") or []
        if not isinstance(cc_list, list):
            continue
        for name in cc_list:
            if isinstance(name, str) and name:
                cc_union.add(name)
    return cc_union


def compute_effective_cc(
    to: str,
    priority: str,
    explicit_cc: list[str] | None,
    routing_rules: list[dict[str, Any]],
    msg_type: str | None = None,
) -> list[str]:
    """Compose the effective CC list per bus-cc-routing Q1.

    - Union of explicit sender ``cc`` and routing-rule-derived ``cc``
    - Deduplicated (set semantics) but returned in a stable insertion order
      so test assertions and the on-disk message stay deterministic
    - The primary ``to`` recipient is excluded even if a rule names them
    """
    seen: set[str] = set()
    ordered: list[str] = []
    candidates: list[str] = []
    if explicit_cc:
        candidates.extend(c for c in explicit_cc if isinstance(c, str) and c)
    candidates.extend(sorted(evaluate_routing_rules(routing_rules, to, priority, msg_type)))
    for name in candidates:
        if name == to or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def inject_x_cc(content: str) -> str:
    """Insert ``x-cc: true`` into the existing frontmatter of *content*.

    The line is appended after the last frontmatter field, before the
    closing ``---`` delimiter. The original message file is never mutated;
    this helper is called only when writing per-recipient CC copies.
    """
    m = re.match(r"^(---\s*\n)(.*?)(\n---)", content, re.DOTALL)
    if not m:
        return content  # malformed frontmatter; caller will not reach here
    head, fm_body, tail = m.group(1), m.group(2), m.group(3)
    new_fm = fm_body.rstrip("\n") + "\nx-cc: true"
    return head + new_fm + tail + content[m.end() :]


__all__ = [
    "compute_effective_cc",
    "evaluate_routing_rules",
    "inject_x_cc",
    "load_routing_rules",
]
