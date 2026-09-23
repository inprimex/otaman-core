"""Tests for the top-level `release:` config key (release-notes-fragments 1.3).

`release.require_human_review` is a spec-approved key that the writer (otaman-deploy)
added to platform.yaml; the schema (additionalProperties:false at root) rejected it,
failing `otaman validate` on otaman-deploy. This pins the schema now accepts it and
still rejects malformed shapes. Distinct from the `releases:` milestone list.
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

PLATFORM_SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "otaman_core"
    / "schemas"
    / "platform-schema.yaml"
)


def _errors(release: object) -> list[str]:
    with open(PLATFORM_SCHEMA, encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    doc = {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "release": release,
    }
    return [e.message for e in jsonschema.Draft7Validator(schema).iter_errors(doc)]


def test_require_human_review_true_validates():
    assert _errors({"require_human_review": True}) == []


def test_require_human_review_false_validates():
    assert _errors({"require_human_review": False}) == []


def test_empty_release_block_validates():
    assert _errors({}) == []


def test_unknown_release_key_is_rejected():
    assert any("Additional properties" in e for e in _errors({"bogus": 1}))


def test_require_human_review_must_be_boolean():
    assert any("boolean" in e for e in _errors({"require_human_review": "yes"}))
