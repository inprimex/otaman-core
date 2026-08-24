"""Tests for the hitl-confirmation-adapters config schema (task 3.1).

Two scopes: program-scope `hitl:` overlay in platform.yaml (narrowing-only) and
the tenant-scope ~/.otaman/hitl.yaml. Covers the no-weakening rule
(program-cannot-enable-insecure) and the default TTY-only posture.
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

from otaman_core.validate_platform import validate_builtin

_SCHEMAS = Path(__file__).resolve().parent.parent / "src" / "otaman_core" / "schemas"
PLATFORM_SCHEMA = _SCHEMAS / "platform-schema.yaml"
HITL_SCHEMA = _SCHEMAS / "hitl-schema.yaml"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _platform(hitl: dict) -> dict:
    return {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        "hitl": hitl,
    }


def _errors(schema: dict, config: dict) -> list[str]:
    return [e.message for e in jsonschema.Draft7Validator(schema).iter_errors(config)]


class TestSchemasParse:
    def test_both_schemas_are_valid_jsonschema(self):
        jsonschema.Draft7Validator.check_schema(_load(PLATFORM_SCHEMA))
        jsonschema.Draft7Validator.check_schema(_load(HITL_SCHEMA))


class TestProgramOverlay:
    def test_approver_roles_narrowing_validates(self):
        schema = _load(PLATFORM_SCHEMA)
        cfg = _platform({"approver_roles": ["cto", "cofounder"]})
        assert _errors(schema, cfg) == []

    def test_program_restating_insecure_false_validates(self):
        schema = _load(PLATFORM_SCHEMA)
        cfg = _platform({"allow_insecure_chat_approval": False})
        assert _errors(schema, cfg) == []

    def test_unknown_hitl_key_rejected(self):
        schema = _load(PLATFORM_SCHEMA)
        cfg = _platform({"approver_roles": ["cto"], "totp_secret_ref": "x"})
        assert _errors(schema, cfg), "hitl is additionalProperties: false"

    def test_no_hitl_is_default_posture(self):
        # absent hitl: preserves TTY-only default — validates clean
        schema = _load(PLATFORM_SCHEMA)
        cfg = {
            "project": "example",
            "version": "1.0",
            "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
        }
        assert _errors(schema, cfg) == []


class TestNoWeakeningRule:
    def test_program_cannot_enable_insecure(self):
        # the keystone: program-scope allow_insecure_chat_approval: true is refused
        errs = validate_builtin(_platform({"allow_insecure_chat_approval": True}))
        assert any("tenant-only" in e and "allow_insecure_chat_approval" in e for e in errs)

    def test_program_restating_false_is_allowed(self):
        errs = validate_builtin(_platform({"allow_insecure_chat_approval": False}))
        assert not any("allow_insecure_chat_approval" in e for e in errs)

    def test_narrowing_only_no_error(self):
        errs = validate_builtin(_platform({"approver_roles": ["cto"]}))
        assert not any("hitl" in e for e in errs)

    def test_hitl_must_be_mapping(self):
        errs = validate_builtin(_platform(["not", "a", "map"]))  # type: ignore[arg-type]
        assert any("hitl" in e and "mapping" in e for e in errs)


class TestTenantSchema:
    def test_full_enrollment_validates(self):
        schema = _load(HITL_SCHEMA)
        cfg = {
            "allow_insecure_chat_approval": False,
            "enrollment": {
                "dev@otaman.ai": {
                    "totp_secret_ref": "hitl-totp-dev",
                    "messenger": {"adapter": "telegram", "address_ref": "hitl-tg-dev"},
                    "idp_subject": "zitadel|12345",
                }
            },
        }
        assert _errors(schema, cfg) == []

    def test_tenant_may_enable_insecure(self):
        # tenant scope owns the strong knob — true is valid here (unlike program)
        schema = _load(HITL_SCHEMA)
        assert _errors(schema, {"allow_insecure_chat_approval": True}) == []

    def test_embedded_value_shape_rejected(self):
        # only *_ref reference keys are allowed; a raw 'totp_secret' is not
        schema = _load(HITL_SCHEMA)
        cfg = {"enrollment": {"dev@otaman.ai": {"totp_secret": "JBSWY3DPEHPK3PXP"}}}
        assert _errors(schema, cfg), "embedded secret value must be rejected (refs only)"

    def test_empty_config_validates(self):
        schema = _load(HITL_SCHEMA)
        assert _errors(schema, {}) == []
