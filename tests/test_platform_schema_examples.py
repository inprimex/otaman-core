"""Schema-drift guard: every example platform.yaml must validate against
the current platform-schema.yaml.

Why this exists: between 2026-04-29 and 2026-05-01, four schema-drift
bugs hit Roman in production (commits d1eeccd, 58564b3, 811bf4e, plus a
related doctor-UX fix in 5abd8ee). Pattern: a new feature ships → a new
top-level field appears in real platform.yaml configs → schema isn't
updated in the same commit → user hits validation error days later.

This test catches that on the same PR that adds the new field, by
validating the curated `examples/*.yaml` files against the live schema.
The contract:

  - Anyone adding a new feature that introduces a top-level platform.yaml
    field must update at least one example/*.yaml to demonstrate it AND
    update assets/platform-schema.yaml to accept it. Both edits in the
    same PR.
  - If the example uses the new field but the schema doesn't accept it,
    this test fails. Reviewer catches it before merge.
  - If neither the example nor the schema knows about the new field,
    that's a documentation gap, not a validation bug — separate problem.

Also covers the `additionalProperties: false` discipline: when a
sub-schema (e.g., standards.git, models.by_repo entry) has `false`,
typos in those sections become test failures here, not silent drift in
production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:
    pytest.skip("PyYAML not installed", allow_module_level=True)

try:
    import jsonschema
except ImportError:
    pytest.skip("jsonschema not installed", allow_module_level=True)


# Carved to otaman-core: schema lives in the package, examples copied as fixtures
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "src" / "otaman_core" / "schemas" / "platform-schema.yaml"
EXAMPLES_DIR = Path(__file__).resolve().parent / "fixtures" / "examples"


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _example_files() -> list[Path]:
    if not EXAMPLES_DIR.is_dir():
        return []
    return sorted(EXAMPLES_DIR.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Schema itself parses


def test_schema_file_exists() -> None:
    assert SCHEMA_PATH.is_file(), f"schema missing at {SCHEMA_PATH}"


def test_schema_is_valid_yaml() -> None:
    schema = _load_schema()
    assert isinstance(schema, dict), "schema must parse to a dict"
    assert schema.get("type") == "object", "schema root must be an object"


def test_schema_is_valid_jsonschema_meta() -> None:
    """The schema document itself must be a valid JSON Schema (Draft 7)."""
    schema = _load_schema()
    # This raises SchemaError if the schema document is malformed.
    jsonschema.Draft7Validator.check_schema(schema)


# ---------------------------------------------------------------------------
# Examples directory has fixtures


def test_examples_directory_not_empty() -> None:
    files = _example_files()
    assert files, (
        f"No example yaml files in {EXAMPLES_DIR} — the schema-drift guard "
        "needs at least one curated example to validate against."
    )


# ---------------------------------------------------------------------------
# Each example validates clean


@pytest.mark.parametrize("example", _example_files(), ids=lambda p: p.name)
def test_example_validates_against_schema(example: Path) -> None:
    """Every examples/*.yaml must validate clean against platform-schema.yaml.

    If this fails, either:
      (a) The example needs updating (it uses a deprecated field), OR
      (b) The schema needs updating (a feature shipped without schema support).

    Look at the failing field. If it's a feature you just added and the
    example demonstrates it correctly, fix the schema in
    `assets/platform-schema.yaml`. If the example was written against
    old behaviour, update the example.
    """
    schema = _load_schema()
    config = _load_yaml(example)
    assert config is not None, f"{example.name} parsed to None"

    errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
    if errors:
        # Build a readable failure message so the reviewer doesn't need to
        # decode raw JSON Schema output.
        msg_lines = [f"{example.name} failed schema validation:"]
        for err in sorted(errors, key=lambda e: list(e.absolute_path)):
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            msg_lines.append(f"  {path}: {err.message}")
        pytest.fail("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# Coverage smoke: examples collectively exercise the major optional sections
#
# We don't enforce that every example uses every field — that's not what
# examples are for. But we do check that across the whole examples/
# directory, the major Phase 6/7/8 optional blocks (git_host, git_platform,
# models, standards, lifecycle, knowledge) appear at least once. If a
# feature ships and zero examples demonstrate it, that's a documentation
# gap worth flagging.


@pytest.mark.parametrize(
    "field",
    [
        "domain",
        "standards",
        "lifecycle",
        "knowledge",
        "specs",
        "observers",
        "communication",
        "profiles",
    ],
)
def test_some_example_uses_field(field: str) -> None:
    """At least one example must use ``field`` at the top level.

    Drives example coverage forward over time without forcing every
    example to be a kitchen sink.
    """
    files = _example_files()
    if not files:
        pytest.skip("no examples to check")
    used = []
    for f in files:
        cfg = _load_yaml(f) or {}
        if field in cfg:
            used.append(f.name)
    assert used, (
        f"No example yaml uses top-level `{field}:` — please add it to one "
        "of the examples so future schema changes have a fixture to validate "
        "against."
    )
