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


# ---------------------------------------------------------------------------
# outcome-proposal-routing — bus.routing_rules[].when.type field
#
# Task 2.2 from openspec/changes/outcome-proposal-routing/tasks.md: confirm
# the schema accepts a `type:` match clause on routing rules, alongside the
# existing `to:` and `priority:` clauses.


def _base_config(when: dict) -> dict:
    """Minimal valid platform.yaml with a single routing rule using `when:`."""
    return {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "bus": {
            "routing_rules": [
                {"when": when, "cc": ["spec-agent"]},
            ],
        },
    }


class TestRoutingRulesWhenTypeField:
    def test_type_only_clause_validates(self):
        schema = _load_schema()
        config = _base_config({"type": "outcome-proposal"})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_type_plus_to_clause_validates(self):
        schema = _load_schema()
        config = _base_config({"type": "outcome-proposal", "to": "human"})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_to_plus_priority_clause_still_validates(self):
        """Existing rule shape (no type:) must remain valid — no regression."""
        schema = _load_schema()
        config = _base_config({"to": "human", "priority": "high"})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_to_plus_priority_list_still_validates(self):
        """Priority as a list is the shape bus-cc-routing already ships."""
        schema = _load_schema()
        config = _base_config({"to": "human", "priority": ["high", "urgent"]})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_all_three_fields_validate(self):
        schema = _load_schema()
        config = _base_config({
            "type": "outcome-proposal",
            "to": "human",
            "priority": "high",
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_unknown_when_field_rejected(self):
        """`when:` is now strict (`additionalProperties: false`)."""
        schema = _load_schema()
        config = _base_config({"type": "outcome-proposal", "from": "agent-a"})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors, "expected validation error for unknown when: field"


# ---------------------------------------------------------------------------
# git-flow-branch-config — standards.git.environments / merge_policy
#
# Tasks 1.1-1.4 from openspec/changes/git-flow-branch-config/tasks.md: schema
# additions for branch-or-tag-to-environment mapping and merge policy, plus
# fixture coverage (valid + invalid) for both. Task 1.4 amends environments[]
# to accept a tag_pattern match key alongside branch (otaman-deploy's real
# release.yml is tag-triggered, not branch-triggered).


def _git_config(git: dict) -> dict:
    """Minimal valid platform.yaml with a `standards.git` block."""
    return {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "standards": {"git": git},
    }


class TestGitFlowBranchConfig:
    def test_environments_valid_dev_main_split_validates(self):
        schema = _load_schema()
        config = _git_config({
            "environments": [
                {"branch": "develop", "environment": "staging", "deploy_trigger": "on_push"},
                {"branch": "main", "environment": "production", "deploy_trigger": "on_merge"},
            ],
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_environments_absent_still_validates(self):
        """Backward compatible: existing standards.git configs need no changes."""
        schema = _load_schema()
        config = _git_config({"branching": "trunk-based"})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_environments_missing_required_field_rejected(self):
        schema = _load_schema()
        config = _git_config({
            "environments": [{"branch": "main", "environment": "production"}],
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors, "expected validation error for missing deploy_trigger"

    def test_environments_invalid_deploy_trigger_rejected(self):
        schema = _load_schema()
        config = _git_config({
            "environments": [
                {"branch": "main", "environment": "production", "deploy_trigger": "on_commit"},
            ],
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors, "expected validation error for unknown deploy_trigger enum value"

    def test_environments_tag_pattern_keyed_entry_validates(self):
        """otaman-deploy's actual shape: no branch, tag-triggered release."""
        schema = _load_schema()
        config = _git_config({
            "environments": [
                {"tag_pattern": "v*", "environment": "production", "deploy_trigger": "on_tag"},
            ],
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_environments_manual_deploy_trigger_validates(self):
        schema = _load_schema()
        config = _git_config({
            "environments": [
                {"tag_pattern": "v*", "environment": "production", "deploy_trigger": "manual"},
            ],
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_environments_neither_branch_nor_tag_pattern_rejected(self):
        schema = _load_schema()
        config = _git_config({
            "environments": [
                {"environment": "production", "deploy_trigger": "on_tag"},
            ],
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors, "expected validation error when neither branch nor tag_pattern is present"

    def test_merge_policy_valid_full_declaration_validates(self):
        schema = _load_schema()
        config = _git_config({
            "merge_policy": {
                "required_checks": ["pytest", "lint"],
                "required_reviews": 1,
                "merge_method": "squash",
            },
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_merge_policy_empty_object_validates(self):
        """All sub-fields of merge_policy are individually optional."""
        schema = _load_schema()
        config = _git_config({"pr_required": True, "merge_policy": {}})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_merge_policy_invalid_merge_method_rejected(self):
        schema = _load_schema()
        config = _git_config({"merge_policy": {"merge_method": "fast-forward"}})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors, "expected validation error for unknown merge_method enum value"

    def test_merge_policy_unknown_field_rejected(self):
        """merge_policy is strict (`additionalProperties: false`)."""
        schema = _load_schema()
        config = _git_config({"merge_policy": {"auto_merge": True}})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors, "expected validation error for unknown merge_policy field"


# ---------------------------------------------------------------------------
# runner-spawn-session-parity — runner.agent_bootstrap.plugin_dir
#
# Task 1.1 from openspec/changes/runner-spawn-session-parity/tasks.md:
# additive, backward-compatible schema field for the org's vendored
# plugin-tree path, forwarded by the runner as `--plugin-dir` on spawn.


def _runner_config(agent_bootstrap: dict) -> dict:
    """Minimal valid platform.yaml with a `runner.agent_bootstrap` block."""
    return {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "runner": {"agent_bootstrap": agent_bootstrap},
    }


class TestRunnerAgentBootstrapPluginDir:
    def test_plugin_dir_absent_validates(self):
        """Backward compatible: existing configs with no plugin_dir still validate."""
        schema = _load_schema()
        config = _runner_config({"mcp_config": ".mcp.json"})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_plugin_dir_valid_string_validates(self):
        schema = _load_schema()
        config = _runner_config({
            "mcp_config": ".mcp.json",
            "system_prompt_append": "CLAUDE.md",
            "plugin_dir": "~/.otaman/otaman-plugin-tree",
        })
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors == [], [e.message for e in errors]

    def test_plugin_dir_wrong_type_rejected(self):
        schema = _load_schema()
        config = _runner_config({"plugin_dir": ["not", "a", "string"]})
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(config))
        assert errors, "expected validation error for non-string plugin_dir"
