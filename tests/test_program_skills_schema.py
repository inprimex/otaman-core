"""Tests for the program.skills activation-config key (skill-activation-config-split 1.2).

program.skills is the skill-pack ACTIVATION CONFIG (which profile is on), distinct
from program.processes.skills, which is reserved for the skills REGISTRY. The key
was documentation/strictness: program is additionalProperties:true, so both keys
already validated — this pins the explicit shape (profile + string extra list,
no stray keys) and confirms the registry slot still validates.
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

_SCHEMAS = Path(__file__).resolve().parent.parent / "src" / "otaman_core" / "schemas"
PLATFORM_SCHEMA = _SCHEMAS / "platform-schema.yaml"


def _validator() -> jsonschema.Draft7Validator:
    with open(PLATFORM_SCHEMA, encoding="utf-8") as f:
        return jsonschema.Draft7Validator(yaml.safe_load(f))


def _errors(program: dict) -> list[str]:
    doc = {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "program": program,
    }
    return [e.message for e in _validator().iter_errors(doc)]


def test_schema_is_itself_valid():
    with open(PLATFORM_SCHEMA, encoding="utf-8") as f:
        jsonschema.Draft7Validator.check_schema(yaml.safe_load(f))


def test_full_activation_config_validates():
    assert (
        _errors({"skills": {"profile": "tech-startup-cofounder", "extra": ["risk-reviewer"]}}) == []
    )


def test_profile_only_validates():
    assert _errors({"skills": {"profile": "tech-startup-cofounder"}}) == []


def test_empty_skills_block_validates():
    assert _errors({"skills": {}}) == []


def test_unknown_key_under_skills_is_rejected():
    errs = _errors({"skills": {"profile": "x", "bogus": 1}})
    assert any("Additional properties" in e and "bogus" in e for e in errs)


def test_profile_must_be_a_string():
    assert any("not of type 'string'" in e for e in _errors({"skills": {"profile": 5}}))


def test_extra_must_be_a_list_of_strings():
    assert any("not of type 'string'" in e for e in _errors({"skills": {"extra": [1, 2]}}))


def test_processes_skills_registry_slot_still_validates():
    """The reserved registry location (program.processes.skills) is untouched —
    program.processes is additionalProperties:true."""
    assert _errors({"processes": {"skills": {"a-skill": {"id": "a-skill"}}}}) == []
