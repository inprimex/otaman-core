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
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

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
}

VALID_PRIORITIES = {"low", "normal", "high", "urgent"}

REQUIRED_FIELDS = {"id", "from", "to", "type", "timestamp"}


def validate_message(filepath: Path, known_agents: set[str] | None = None) -> list[str]:
    """Validate a single bus message file. Returns list of errors."""
    errors: list[str] = []

    try:
        content = filepath.read_text(encoding="utf-8")
    except OSError as e:
        return [f"Cannot read file: {e}"]

    # Parse frontmatter
    fm_match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
    if not fm_match:
        return ["Missing YAML frontmatter (expected --- ... --- at start of file)"]

    try:
        fm = yaml.safe_load(fm_match.group(1))
    except yaml.YAMLError as e:
        return [f"Invalid YAML frontmatter: {e}"]

    if not isinstance(fm, dict):
        return ["Frontmatter must be a YAML mapping"]

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"Missing required field: {field}")

    # Type validation
    msg_type = fm.get("type")
    if msg_type and msg_type not in VALID_TYPES:
        errors.append(f"Unknown type: '{msg_type}' (valid: {', '.join(sorted(VALID_TYPES))})")

    # Priority validation
    priority = fm.get("priority")
    if priority and priority not in VALID_PRIORITIES:
        errors.append(f"Unknown priority: '{priority}' (valid: {', '.join(sorted(VALID_PRIORITIES))})")

    # Status validation (legacy field, still accepted)
    status = fm.get("status")
    if status and status not in ("pending", "read", "resolved"):
        errors.append(f"Unknown status: '{status}' (valid: pending, read, resolved)")

    # Agent name validation
    if known_agents:
        to_field = fm.get("to", "")
        if to_field and to_field not in ("all", "human") and to_field not in known_agents:
            errors.append(f"Unknown recipient agent: '{to_field}' (not in agents.yaml)")

        from_field = fm.get("from", "")
        if from_field and from_field not in ("human",) and from_field not in known_agents:
            # Allow repo names as senders (from post-commit hooks)
            pass  # Don't error on unknown senders — hooks use repo names

    # Subject line check
    body = content.split("---", 2)[-1] if content.count("---") >= 2 else ""
    has_subject = any(
        line.strip().startswith("## Subject:")
        for line in body.splitlines()
    )
    if not has_subject:
        errors.append("Missing '## Subject:' line in message body")

    return errors


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


from otaman_core._resolve import find_maestro_root as find_project_root  # shared resolver


def main() -> int:
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
    for filepath in files_to_check:
        errors = validate_message(filepath, known_agents)
        if errors:
            total_errors += len(errors)
            print(f"INVALID: {filepath.name}")
            for err in errors:
                print(f"  {err}")
        else:
            print(f"OK: {filepath.name}")

    if total_errors:
        print(f"\n{total_errors} error(s) in {len(files_to_check)} file(s)")
        return 1
    else:
        print(f"\nAll {len(files_to_check)} message(s) valid")
        return 0


if __name__ == "__main__":
    sys.exit(main())
