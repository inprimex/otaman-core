"""Schema tests for the security-gates: block (security-gates-hook-c 1.1).

Pins the platform-schema.yaml shape: the five layers, the 2-of-3 scanner enum, the
per-repo opt-out/overrides, and `additionalProperties: false` on the layer/repo
objects so typos surface via `otaman validate`. The core parser
(otaman_core.security_gates) does the strict semantic validation; this guards the
JSON-schema mirror.
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


def _errors(security_gates: dict) -> list[str]:
    doc = {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "security-gates": security_gates,
    }
    return [e.message for e in _validator().iter_errors(doc)]


def test_schema_is_itself_valid():
    with open(PLATFORM_SCHEMA, encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    jsonschema.Draft7Validator.check_schema(schema)


def test_full_block_validates():
    assert (
        _errors(
            {
                "relocation-note": "moves under JTBD-59",
                "languages": {
                    "python": {
                        "pre-commit": {"tools": ["ruff"], "blocking": True},
                        "ci-medium": {
                            "tools": ["semgrep"],
                            "scanner-pair": ["trivy", "grype"],
                            "timeout": 300,
                            "blocking": True,
                        },
                        "llm-observer": {"blocking": False, "cost-cap": 0.5},
                    }
                },
                "repos": {
                    "backend": {"languages": ["python"], "ci-slow": {"opt-in": True}},
                    "docs": {"opt-out": True},
                },
            }
        )
        == []
    )


def test_unknown_layer_rejected():
    assert _errors({"languages": {"python": {"ci-instant": {"tools": []}}}})


def test_unknown_layer_field_rejected():
    assert _errors({"languages": {"python": {"ci-fast": {"toolz": ["x"]}}}})


def test_scanner_pair_enum_enforced():
    assert _errors({"languages": {"python": {"ci-medium": {"scanner-pair": ["trivy", "snyk"]}}}})


def test_scanner_pair_size_enforced():
    assert _errors({"languages": {"python": {"ci-medium": {"scanner-pair": ["trivy"]}}}})


def test_repo_unknown_key_rejected():
    assert _errors({"repos": {"backend": {"opt_out": True}}})  # hyphen, not underscore


def test_example_fixture_carries_the_block():
    # the drift-guard contract: the demonstrating example must actually use it
    example = Path(__file__).resolve().parent / "fixtures" / "examples" / "example-platform.yaml"
    with open(example, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    assert "security-gates" in doc
    assert _errors(doc["security-gates"]) == []
