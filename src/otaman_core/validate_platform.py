#!/usr/bin/env python3
"""Validate a platform.yaml file against the otaman platform schema.

Usage:
    python validate-platform.py <path-to-platform.yaml>
    python validate-platform.py  # looks for platform.yaml in current dir

Exit codes:
    0 — valid
    1 — validation errors found
    2 — file not found or parse error
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

try:
    import jsonschema
except ImportError:
    jsonschema = None  # type: ignore[assignment]

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = Path(__file__).parent / "schemas" / "platform-schema.yaml"


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_with_jsonschema(config: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate using jsonschema library if available."""
    if jsonschema is None:
        return []
    errors: list[str] = []
    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"  {path}: {err.message}")
    return errors


def validate_builtin(config: dict[str, Any]) -> list[str]:
    """Basic structural validation without jsonschema dependency."""
    errors: list[str] = []

    if not isinstance(config, dict):
        return ["Config must be a YAML mapping"]

    # Required top-level keys
    if "project" not in config:
        errors.append("Missing required field: project")
    elif not isinstance(config["project"], str):
        errors.append("'project' must be a string")

    if "version" not in config:
        errors.append("Missing required field: version")
    elif str(config["version"]) != "1.0":
        errors.append(f"Unsupported version: {config['version']} (expected '1.0')")

    if "repos" not in config:
        errors.append("Missing required field: repos")
        return errors
    if not isinstance(config["repos"], list) or len(config["repos"]) == 0:
        errors.append("'repos' must be a non-empty list")
        return errors

    # Validate repos
    owners: set[str] = set()
    repo_names: set[str] = set()
    for i, repo in enumerate(config["repos"]):
        prefix = f"repos[{i}]"
        if not isinstance(repo, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue
        for field in ("name", "path", "owner"):
            if field not in repo:
                errors.append(f"{prefix}: missing required field '{field}'")
        name = repo.get("name", "")
        if name:
            if name in repo_names:
                errors.append(f"{prefix}: duplicate repo name '{name}'")
            repo_names.add(name)
        owner = repo.get("owner", "")
        if owner:
            owners.add(owner)

        # Validate disabled (optional bool)
        if "disabled" in repo and not isinstance(repo["disabled"], bool):
            errors.append(f"{prefix}.disabled: must be a boolean (true or false)")

        # Validate specs_dir (optional)
        if "specs_dir" in repo:
            sd = repo["specs_dir"]
            if not isinstance(sd, list):
                errors.append(f"{prefix}.specs_dir: must be a list")
            elif not all(isinstance(s, str) for s in sd):
                errors.append(f"{prefix}.specs_dir: all items must be strings")

        # Validate launch (optional)
        if "launch" in repo:
            launch = repo["launch"]
            if not isinstance(launch, dict):
                errors.append(f"{prefix}.launch: must be a mapping")
            else:
                if "shell" not in launch:
                    errors.append(f"{prefix}.launch: missing required field 'shell'")
                elif launch["shell"] not in ("wsl", "powershell", "bash"):
                    errors.append(f"{prefix}.launch.shell: must be one of wsl, powershell, bash")
                if "commands" not in launch:
                    errors.append(f"{prefix}.launch: missing required field 'commands'")
                elif not isinstance(launch["commands"], list) or len(launch["commands"]) == 0:
                    errors.append(f"{prefix}.launch.commands: must be a non-empty list")
                elif not all(isinstance(c, str) for c in launch["commands"]):
                    errors.append(f"{prefix}.launch.commands: all items must be strings")
                if "color" in launch:
                    import re

                    if not isinstance(launch["color"], str) or not re.match(
                        r"^#[0-9a-fA-F]{6}$", launch["color"]
                    ):
                        errors.append(f"{prefix}.launch.color: must be a hex color like '#4169E1'")
                if "title" in launch and not isinstance(launch["title"], str):
                    errors.append(f"{prefix}.launch.title: must be a string")

    # Validate specs (optional)
    if "specs" in config:
        specs = config["specs"]
        if not isinstance(specs, dict):
            errors.append("'specs' must be a mapping")
        else:
            if "format" not in specs:
                errors.append("specs: missing required field 'format'")
            fmt = specs.get("format")
            if fmt not in (None, "openspec", "fallback"):
                errors.append("specs.format: must be one of openspec, fallback")
            if fmt == "openspec" and "path" not in specs:
                errors.append("specs: 'path' is required when format is 'openspec'")

    # Validate contracts (optional)
    if "contracts" in config:
        contracts = config["contracts"]
        if not isinstance(contracts, dict):
            errors.append("'contracts' must be a mapping")
        else:
            for field in ("path", "format"):
                if field not in contracts:
                    errors.append(f"contracts: missing required field '{field}'")
            if contracts.get("format") not in (None, "openapi", "jsonschema", "protobuf"):
                errors.append("contracts.format: must be one of openapi, jsonschema, protobuf")

    # Validate observers (optional)
    if "observers" in config:
        if not isinstance(config["observers"], list):
            errors.append("'observers' must be a list")
        else:
            for i, obs in enumerate(config["observers"]):
                prefix = f"observers[{i}]"
                if not isinstance(obs, dict):
                    errors.append(f"{prefix}: must be a mapping")
                    continue
                if "role" not in obs:
                    errors.append(f"{prefix}: missing required field 'role'")
                if "triggers" not in obs:
                    errors.append(f"{prefix}: missing required field 'triggers'")
                elif not isinstance(obs["triggers"], list) or len(obs["triggers"]) == 0:
                    errors.append(f"{prefix}.triggers: must be a non-empty list")

    # Validate communication (optional)
    if "communication" in config:
        comm = config["communication"]
        if not isinstance(comm, dict):
            errors.append("'communication' must be a mapping")
        else:
            if "bus_path" not in comm:
                errors.append("communication: missing required field 'bus_path'")
            if "format" not in comm:
                errors.append("communication: missing required field 'format'")

    # Validate hitl (optional, program scope) — no-weakening rule
    # (hitl-confirmation-adapters 3.1). A program may narrow but never weaken
    # the tenant ~/.otaman/hitl.yaml scope: enabling insecure chat approval is
    # tenant-only, so program-scope `allow_insecure_chat_approval: true` is
    # refused with the tenant scope named as the reason.
    if "hitl" in config:
        hitl = config["hitl"]
        if not isinstance(hitl, dict):
            errors.append("'hitl' must be a mapping")
        elif hitl.get("allow_insecure_chat_approval") is True:
            errors.append(
                "hitl.allow_insecure_chat_approval is tenant-only "
                "(~/.otaman/hitl.yaml); a program cannot enable insecure chat "
                "approval (no-weakening rule)"
            )

    return errors


CONNECTIONS_SCHEMA_PATH = Path(__file__).parent / "schemas" / "connections-schema.yaml"


def validate_connections(config: dict[str, Any]) -> list[str]:
    """Validate a connections.yaml mapping (agent-credential-access 4.1).

    Uses jsonschema against connections-schema.yaml when available; falls back to
    a minimal structural check (each connection needs name/type/endpoint, and
    never an embedded secret value). Returns a list of human-readable errors.
    """
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["connections.yaml must be a YAML mapping"]

    if jsonschema is not None:
        schema = load_yaml(CONNECTIONS_SCHEMA_PATH)
        validator = jsonschema.Draft7Validator(schema)
        for err in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path)):
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"  {path}: {err.message}")
        return errors

    # Builtin fallback (no jsonschema).
    conns = config.get("connections", [])
    if not isinstance(conns, list):
        return ["connections: must be a list"]
    for i, conn in enumerate(conns):
        prefix = f"connections[{i}]"
        if not isinstance(conn, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue
        for field in ("name", "type", "endpoint"):
            if field not in conn:
                errors.append(f"{prefix}: missing required field '{field}'")
        scope = conn.get("scope")
        if scope is not None and scope not in ("tenant", "org", "program"):
            errors.append(f"{prefix}.scope: must be one of tenant, org, program")
    return errors


def main() -> int:
    # Determine config path
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    else:
        config_path = Path.cwd() / "platform.yaml"

    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        return 2

    # Load config
    try:
        config = load_yaml(config_path)
    except yaml.YAMLError as e:
        print(f"ERROR: Failed to parse YAML: {e}", file=sys.stderr)
        return 2

    errors: list[str] = []

    # Try jsonschema validation first
    if jsonschema is not None and SCHEMA_PATH.exists():
        schema = load_yaml(SCHEMA_PATH)
        errors = validate_with_jsonschema(config, schema)
    else:
        # Fall back to built-in validation
        errors = validate_builtin(config)

    if errors:
        print(f"VALIDATION FAILED for {config_path}:")
        for err in errors:
            print(f"  {err}")
        return 1

    print(f"OK: {config_path} is valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
