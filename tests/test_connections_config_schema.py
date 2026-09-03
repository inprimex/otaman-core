"""Tests for the agent-credential-access layered config schema (task 4.1).

Covers the new connections.yaml schema + validate_connections, and the shared
`secret_backend:` key declared in platform-schema.yaml to avoid collision with
pluggable-secret-backend.
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

from otaman_core.validate_platform import validate_connections

_SCHEMAS = Path(__file__).resolve().parent.parent / "src" / "otaman_core" / "schemas"
PLATFORM_SCHEMA = _SCHEMAS / "platform-schema.yaml"
CONNECTIONS_SCHEMA = _SCHEMAS / "connections-schema.yaml"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _platform(**extra) -> dict:
    base = {
        "project": "example",
        "version": "1.0",
        "repos": [{"name": "repo-a", "path": "../repo-a", "owner": "agent-a"}],
    }
    base.update(extra)
    return base


def _perrors(config: dict) -> list[str]:
    schema = _load(PLATFORM_SCHEMA)
    return [e.message for e in jsonschema.Draft7Validator(schema).iter_errors(config)]


class TestSchemaParses:
    def test_connections_schema_is_valid_jsonschema(self):
        jsonschema.Draft7Validator.check_schema(_load(CONNECTIONS_SCHEMA))


class TestSharedSecretBackendKey:
    def test_platform_accepts_secret_backend_block(self):
        # the shared key with pluggable-secret-backend — declared, permissive
        cfg = _platform(secret_backend={"type": "env-file", "path": ".otaman/secrets.env"})
        assert _perrors(cfg) == []

    def test_no_secret_backend_still_valid(self):
        assert _perrors(_platform()) == []


class TestConnectionsValid:
    def test_full_connection_validates(self):
        cfg = {
            "connections": [
                {
                    "name": "github-primary",
                    "type": "git-https",
                    "endpoint": "github.com",
                    "secret_ref": "gh-pat",
                    "scope": "org",
                },
                {
                    "name": "sunflowers-ssh",
                    "type": "ssh",
                    "endpoint": "sunflowers.example.com",
                    "ssh_ref": "sunflowers",
                    "scope": "program",
                },
            ]
        }
        assert validate_connections(cfg) == []

    def test_empty_connections_valid(self):
        assert validate_connections({"connections": []}) == []
        assert validate_connections({}) == []

    def test_kind_and_ssh_scope_validate(self):
        # 1.2 additions: `kind` (enum) + `ssh_scope` (note) on the ssh pointer.
        cfg = {
            "connections": [
                {
                    "name": "client-prod",
                    "type": "ssh",
                    "endpoint": "client-prod.example.com",
                    "kind": "deploy-key",
                    "ssh_ref": "client-prod-deploy",
                    "ssh_scope": "prod deploy, read-only",
                    "scope": "program",
                }
            ]
        }
        assert validate_connections(cfg) == []

    def test_every_ruled_kind_validates(self):
        for kind in ("pat", "deploy-key", "api-key", "oauth", "ssh"):
            cfg = {
                "connections": [{"name": "conn-x", "type": "api", "endpoint": "e", "kind": kind}]
            }
            assert validate_connections(cfg) == [], f"kind {kind!r} should validate"


class TestConnectionsInvalid:
    def test_bad_kind_rejected(self):
        cfg = {"connections": [{"name": "conn-x", "type": "api", "endpoint": "e", "kind": "token"}]}
        assert validate_connections(cfg), "kind must be one of the ruled enum"

    def test_missing_required_field_rejected(self):
        cfg = {"connections": [{"name": "x-conn", "type": "api"}]}  # no endpoint
        assert validate_connections(cfg), "endpoint is required"

    def test_bad_scope_rejected(self):
        cfg = {
            "connections": [
                {"name": "x-conn", "type": "api", "endpoint": "x.com", "scope": "galaxy"}
            ]
        }
        assert validate_connections(cfg), "scope must be tenant|org|program"

    def test_embedded_value_field_rejected(self):
        # only *_ref reference fields allowed; a raw 'secret' is not (refs, not values)
        cfg = {
            "connections": [
                {"name": "x-conn", "type": "api", "endpoint": "x.com", "secret": "s3cr3t"}
            ]
        }
        assert validate_connections(cfg), "embedded secret value must be rejected"

    def test_bad_name_pattern_rejected(self):
        cfg = {"connections": [{"name": "Bad Name", "type": "api", "endpoint": "x.com"}]}
        assert validate_connections(cfg), "name must match slug pattern"

    def test_non_mapping_rejected(self):
        assert validate_connections(["not", "a", "map"])  # type: ignore[arg-type]
