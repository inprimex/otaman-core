"""spec_policy is a canon, core-read program-level block — platform-schema must accept it.

Regression for the 2026-10-02 defect: the key was missing from platform-schema.yaml
(additionalProperties: false), so `otaman init` / `otaman validate` failed on any
program carrying it. resolve_spec_policy is the authority on the shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    pytest.skip("PyYAML not installed", allow_module_level=True)

try:
    import jsonschema
except ImportError:  # pragma: no cover
    pytest.skip("jsonschema not installed", allow_module_level=True)

_SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "otaman_core"
    / "schemas"
    / "platform-schema.yaml"
)


def _errors(spec_policy) -> list[str]:
    with open(_SCHEMA, encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    doc = {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "spec_policy": spec_policy,
    }
    return [e.message for e in jsonschema.Draft7Validator(schema).iter_errors(doc)]


def test_full_spec_policy_validates():
    assert (
        _errors(
            {
                "enforcement": "block",
                "process": {"level": "outcomes"},
                "delivery_default": "hitl",
                "mandatory_proposal": True,
                "approvers": ["cto"],
                "ratifiers": ["approver"],
                "unapproved_stages": ["pre-proposal"],
            }
        )
        == []
    )


def test_enforcement_as_per_action_map_validates():
    assert _errors({"enforcement": {"merge": "block", "dispatch": "warn"}}) == []


def test_bad_enforcement_rejected():
    assert _errors({"enforcement": "nuke"})


def test_bad_level_rejected():
    assert _errors({"process": {"level": "l9"}})


def test_stray_key_rejected():
    assert _errors({"enforcment": "block"})  # misspelled key -> additionalProperties:false
