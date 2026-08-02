#!/usr/bin/env python3
"""Validate bus message files against the otaman message schema.

Usage:
    python validate-message.py <message-file>           # validate one file
    python validate-message.py <bus-active-dir>          # validate all .md in dir
    python validate-message.py <project-root> --all      # validate all active messages

Exit codes:
    0 — all valid
    1 — validation errors found
    2 — file/dir not found
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# Bus message frontmatter schema
# ---------------------------------------------------------------------------
# Required fields: id, from, to, type, timestamp
#
# Optional fields:
#
#   priority:   low | normal | high | urgent  (default: normal if absent)
#   status:     pending | read | resolved     (managed by the CLI; agents
#               should not set this manually)
#
#   reply-to:   <agent-name> | human
#     Present on task-assignment messages to declare which agent should
#     receive the task-complete reply. When absent, `otaman complete`
#     falls back to the `from:` field of the originating task-assignment.
#     Optional; valid values follow the same agent-name pattern as `from:`.
#
#   to:         all | human | <agent-name> | <agent-name>, <agent-name>, ...
#     Single agent name, comma-separated list, `human`, or `all`.
#     Broadcast whitelist — only these types may use `to: all`:
#       - contract-change
#       - emergency-halt
#       - agent-registry-change
#     Any other type using `to: all` triggers a validation error.
#
#   expects-response: true | false
#     Sender declares whether a reply is required. Default false (FYI semantics
#     preserved when absent). When true, the receiver MUST NOT ack as `resolved`
#     until a typed reply is sent. Reply routes via `reply-to:` (fallback: `from:`).
#     Constraint: `task-assignment` type MUST NOT set `expects-response: false`
#     (task-complete reply is always implied). Warning emitted when used with `to: all`.
#
#   response-effort: XS | S | M | L | XL
#     T-shirt sizing of expected receiver effort. Used by `otaman check` as a
#     tiebreaker within the same priority band (cheapest first). Defaults are
#     inferred from message type if absent.
#
#   response-deadline: <RFC-3339 with timezone>
#     Wall-clock SLA for the reply. ONLY meaningful when a human is in the
#     reply path (release windows, regulatory deadlines). Not meaningful for
#     agent-to-agent traffic — use `response-effort` for AI-to-AI ordering.
#     Example: 2026-06-04T18:00:00Z
#
#   repo: <repo-name>
#     Repo context for path-based dispatch. Required when `path:` is present;
#     otherwise optional and informational. Must match a `repos[].name` in
#     platform.yaml at dispatch time (loosely checked here — the dispatcher
#     enforces the strict check).
#
#   path: <relative-path> | [<path>, <path>, ...]
#     Optional, monorepo-path-ownership feature. When present, the bus
#     resolves the recipient via `owner-paths` on the named `repo:`,
#     overriding the `to:` field. Allowed only on these message types:
#       - task-assignment
#       - task-complete
#       - spec-change-request
#       - contract-change
#     For other types using `path:` is rejected — they have semantics
#     that don't fit per-path routing.
#
# Body format: Markdown. MUST contain a `## Subject: <text>` heading.
# ---------------------------------------------------------------------------

VALID_TYPES = {
    "info",
    "question",
    "contract-change",
    "spec-change",
    "spec-change-request",
    "spec-change-approved",
    "spec-change-rejected",
    "review-request",
    "task-assignment",
    "task-complete",
    "proposal",
    "post-commit-review",
    "emergency-halt",
    "agent-registry-change",
    # auto-session-spawn-on-bus-events §Q4
    "request-human-review",
    "human-decision",
    # outcome-and-solution-registries Appendix F.3 + G
    "outcome-estimate-requested",
    "outcome-estimates-ready",
    "outcome-cost-accepted",
    "outcome-cost-rejected",
    "outcome-status-changed",
    "solution-status-changed",
    "solution-recommendation",
    # outcome-proposal-routing
    "outcome-proposal",
}

# Privileged types: these assert that a human made a decision. Forging one
# defeats the platform's HITL guarantee (security GAP finding F012, 2026-07-04)
# — the validator therefore requires from: human on all of them.
PRIVILEGED_TYPES = frozenset({
    "human-decision",
    "spec-change-approved",
    "spec-change-rejected",
    "emergency-halt",
})

_HUMAN_SENDER = "human"

VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_RESPONSE_EFFORTS = {"XS", "S", "M", "L", "XL"}

# Types that may carry a `path:` field for monorepo-path-ownership routing.
PATH_ELIGIBLE_TYPES = frozenset({
    "task-assignment",
    "task-complete",
    "spec-change-request",
    "contract-change",
})

REQUIRED_FIELDS = {"id", "from", "to", "type", "timestamp"}

# RFC 3339 / ISO-8601 with mandatory timezone offset or Z
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(\.\d+)?"
    r"(Z|[+-]\d{2}:\d{2})$"
)


_BROADCAST_TYPES = frozenset({"contract-change", "emergency-halt", "agent-registry-change"})
_REPLY_TO_PATTERN = re.compile(r"^[a-z][a-z0-9-]+-agent$|^human$")


def validate_message(
    filepath: Path, known_agents: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """Validate a single bus message file.

    Returns (errors, warnings). Errors block the message; warnings are advisory.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except OSError as e:
        return [f"Cannot read file: {e}"], []

    return validate_message_content(content, known_agents)


def validate_message_content(
    content: str, known_agents: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """Validate bus message *content* directly, without requiring a file on disk.

    This is the core the file-based :func:`validate_message` wraps. Exposing
    it separately lets callers validate a message **before** it is written —
    e.g. a PreToolUse hook can pass the ``tool_input.content`` of a pending
    Write/Edit targeting ``.agents/bus/**`` here and block the call on error,
    which file-based validation cannot do (it only ever runs after the file
    already exists). See ``main()``'s ``--stdin`` mode for the CLI entry
    point a non-Python hook can shell out to.

    Returns (errors, warnings). Errors block the message; warnings are advisory.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Parse frontmatter
    fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
    if not fm_match:
        return ["Missing YAML frontmatter (expected --- ... --- at start of file)"], []

    try:
        fm = yaml.safe_load(fm_match.group(1))
    except yaml.YAMLError as e:
        return [f"Invalid YAML frontmatter: {e}"], []

    if not isinstance(fm, dict):
        return ["Frontmatter must be a YAML mapping"], []

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"Missing required field: {field}")

    # Type validation
    msg_type = fm.get("type")
    if msg_type and msg_type not in VALID_TYPES:
        errors.append(f"Unknown type: '{msg_type}' (valid: {', '.join(sorted(VALID_TYPES))})")

    # F012: privileged types assert a human decision — only from: human may send them.
    if msg_type in PRIVILEGED_TYPES:
        from_field = fm.get("from")
        if from_field != _HUMAN_SENDER:
            errors.append(
                f"type '{msg_type}' is privileged and may only be sent with from: human "
                f"(got from: {from_field!r}); this guards the platform's HITL guarantee"
            )

    # Priority validation
    priority = fm.get("priority")
    if priority and priority not in VALID_PRIORITIES:
        errors.append(f"Unknown priority: '{priority}' (valid: {', '.join(sorted(VALID_PRIORITIES))})")

    # Status validation (legacy field, still accepted)
    status = fm.get("status")
    if status and status not in ("pending", "read", "resolved"):
        errors.append(f"Unknown status: '{status}' (valid: pending, read, resolved)")

    # reply-to: optional routing field
    reply_to = fm.get("reply-to")
    if reply_to is not None:
        if not _REPLY_TO_PATTERN.match(str(reply_to)):
            errors.append(
                f"Invalid reply-to: '{reply_to}' — must be an agent name "
                "(e.g. 'core-agent') or 'human'"
            )

    # expects-response: optional boolean
    expects_response = fm.get("expects-response")
    if expects_response is not None:
        if not isinstance(expects_response, bool):
            errors.append(
                f"Invalid expects-response: '{expects_response}' — must be a boolean (true or false)"
            )
        elif not expects_response and msg_type == "task-assignment":
            errors.append(
                "task-assignment messages implicitly require a task-complete reply; "
                "setting expects-response: false is invalid"
            )

    # response-effort: optional enum
    response_effort = fm.get("response-effort")
    if response_effort is not None:
        if str(response_effort) not in VALID_RESPONSE_EFFORTS:
            errors.append(
                f"Invalid response-effort: '{response_effort}' "
                f"(valid: {', '.join(sorted(VALID_RESPONSE_EFFORTS))})"
            )

    # response-deadline: optional RFC 3339 with timezone
    response_deadline = fm.get("response-deadline")
    if response_deadline is not None:
        if not _RFC3339_RE.match(str(response_deadline)):
            errors.append(
                f"Invalid response-deadline: '{response_deadline}' — "
                "must be RFC 3339 / ISO-8601 with timezone (e.g. 2026-06-04T18:00:00Z)"
            )

    # path: optional monorepo-path-ownership routing field
    path_field = fm.get("path")
    if path_field is not None:
        if msg_type and msg_type not in PATH_ELIGIBLE_TYPES:
            errors.append(
                f"'path:' field is not allowed on type '{msg_type}'; "
                f"only {sorted(PATH_ELIGIBLE_TYPES)} may use path-based routing"
            )
        if isinstance(path_field, str):
            if not path_field.strip():
                errors.append("path: must be a non-empty string when scalar")
        elif isinstance(path_field, list):
            if len(path_field) == 0:
                errors.append("path: list must contain at least one entry")
            elif not all(isinstance(p, str) and p.strip() for p in path_field):
                errors.append("path: every list entry must be a non-empty string")
        else:
            errors.append(
                f"path: must be a string or a list of strings, "
                f"got {type(path_field).__name__}"
            )
        # path requires repo: context for the dispatcher to resolve owner-paths.
        repo_field = fm.get("repo")
        if not isinstance(repo_field, str) or not repo_field.strip():
            errors.append(
                "'path:' requires a 'repo:' field naming the platform.yaml "
                "repo whose owner-paths the path is relative to"
            )

    # to: field — agent name validation and broadcast whitelist
    to_field = fm.get("to", "")

    if known_agents:
        if to_field and to_field not in ("all", "human"):
            recipients = [r.strip() for r in to_field.split(",") if r.strip()]
            unknown = [r for r in recipients if r not in known_agents and r not in ("human", "all")]
            if unknown:
                errors.append(
                    f"Unknown recipient agent(s): {unknown!r} (not in agents.yaml)"
                )

    # Broadcast whitelist check (always; not gated on known_agents)
    msg_type = fm.get("type", "")
    if to_field == "all" and msg_type and msg_type not in _BROADCAST_TYPES:
        errors.append(
            f"Message type '{msg_type}' must not use 'to: all'; "
            f"only {sorted(_BROADCAST_TYPES)} may broadcast. "
            "Use a specific agent name or comma-separated list instead."
        )

    # Warning: broadcast + expects-response: true is best-effort, not a strict contract
    if to_field == "all" and expects_response is True:
        warnings.append(
            "broadcast with expects-response: true is best-effort; "
            "verify you intend multi-response semantics"
        )

    # Subject line check
    body = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
    has_subject = any(
        line.strip().startswith("## Subject:")
        for line in body.splitlines()
    )
    if not has_subject:
        errors.append("Missing '## Subject:' line in message body")

    return errors, warnings


def load_known_agents(project_root: Path) -> set[str]:
    """Load agent names from agents.yaml if available."""
    agents_file = project_root / ".agents" / "agents.yaml"
    if not agents_file.exists():
        return set()
    try:
        with open(agents_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "agents" in data:
            return {a["name"] for a in data["agents"] if isinstance(a, dict) and "name" in a}
    except (OSError, yaml.YAMLError):
        pass
    return set()


from otaman_core._resolve import (  # noqa: E402 — shared resolver re-export
    find_maestro_root as find_project_root,
)


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--stdin":
        # Validate content read from stdin, without a file on disk. Intended
        # for a PreToolUse hook to shell out to before allowing a Write/Edit
        # into .agents/bus/** to complete (F012): e.g.
        #   python3 -m otaman_core.validate_message --stdin <<< "$tool_input_content"
        content = sys.stdin.read()
        project_root = find_project_root(Path.cwd())
        known_agents = load_known_agents(project_root) if project_root else set()
        errors, warnings = validate_message_content(content, known_agents)
        for w in warnings:
            print(f"WARNING: {w}", file=sys.stderr)
        if errors:
            for e in errors:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print("OK")
        return 0

    if len(sys.argv) < 2:
        print("Usage: validate-message.py <message-file|bus-dir|project-root> [--all]", file=sys.stderr)
        return 2

    target = Path(sys.argv[1]).resolve()
    validate_all = "--all" in sys.argv

    # Determine what to validate
    files_to_check: list[Path] = []

    if target.is_file():
        files_to_check = [target]
    elif target.is_dir():
        if validate_all:
            # Treat as project root
            bus_dir = target / ".agents" / "bus" / "active"
            if not bus_dir.is_dir():
                print(f"ERROR: No bus directory at {bus_dir}", file=sys.stderr)
                return 2
            files_to_check = sorted(bus_dir.glob("*.md"))
        else:
            # Treat as bus directory
            files_to_check = sorted(target.glob("*.md"))
    else:
        print(f"ERROR: Not found: {target}", file=sys.stderr)
        return 2

    if not files_to_check:
        print("No message files found.")
        return 0

    # Try to load known agents for richer validation
    project_root = find_project_root(target)
    known_agents = load_known_agents(project_root) if project_root else set()

    total_errors = 0
    total_warnings = 0
    for filepath in files_to_check:
        errors, warnings = validate_message(filepath, known_agents)
        if errors:
            total_errors += len(errors)
            total_warnings += len(warnings)
            print(f"INVALID: {filepath.name}")
            for err in errors:
                print(f"  ERROR: {err}")
            for warn in warnings:
                print(f"  WARNING: {warn}")
        elif warnings:
            total_warnings += len(warnings)
            print(f"WARNING: {filepath.name}")
            for warn in warnings:
                print(f"  WARNING: {warn}")
        else:
            print(f"OK: {filepath.name}")

    if total_errors:
        print(f"\n{total_errors} error(s), {total_warnings} warning(s) in {len(files_to_check)} file(s)")
        return 1
    elif total_warnings:
        print(f"\n0 errors, {total_warnings} warning(s) in {len(files_to_check)} file(s)")
        return 0
    else:
        print(f"\nAll {len(files_to_check)} message(s) valid")
        return 0


if __name__ == "__main__":
    sys.exit(main())
