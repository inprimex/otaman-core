"""Tests for the connection cascade resolver (agent-credential-access 1.1).

Covers the Connection model, per-name tenant→org→program cascade merge (nearest
scope wins, unrelated broader connections still resolve), the values-never-exposed
invariant (only *_ref locators surface), and the list_keys()-based inventory check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ImportError:
    pytest.skip("PyYAML not installed", allow_module_level=True)

from otaman_core.connections import (
    KINDS,
    Connection,
    ConnectionsError,
    default_scope_files,
    missing_secret_refs,
    parse_connections,
    resolve,
    resolve_for,
)


def _write(path: Path, connections: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"connections": connections}), encoding="utf-8")


class TestParseConnections:
    def test_parses_full_connection(self):
        data = {
            "connections": [
                {
                    "name": "github-primary",
                    "type": "git-https",
                    "endpoint": "github.com",
                    "secret_ref": "gh-pat",
                    "scope": "org",
                }
            ]
        }
        conns = parse_connections(data)
        assert conns == [
            Connection(
                name="github-primary",
                type="git-https",
                endpoint="github.com",
                scope="org",
                secret_ref="gh-pat",
            )
        ]

    def test_default_scope_labels_scopeless_connection(self):
        data = {"connections": [{"name": "x-conn", "type": "api", "endpoint": "x.com"}]}
        (conn,) = parse_connections(data, default_scope="tenant")
        assert conn.scope == "tenant"

    def test_none_and_empty_yield_no_connections(self):
        assert parse_connections(None) == []
        assert parse_connections({}) == []
        assert parse_connections({"connections": []}) == []

    def test_missing_required_field_raises(self):
        with pytest.raises(ConnectionsError, match="endpoint"):
            parse_connections({"connections": [{"name": "x-conn", "type": "api"}]})

    def test_bad_scope_raises(self):
        data = {
            "connections": [
                {"name": "x-conn", "type": "api", "endpoint": "x.com", "scope": "galaxy"}
            ]
        }
        with pytest.raises(ConnectionsError, match="scope"):
            parse_connections(data)

    def test_non_mapping_raises(self):
        with pytest.raises(ConnectionsError):
            parse_connections(["not", "a", "map"])


class TestCascade:
    def test_program_overrides_org_for_one_key_others_still_resolve(self, tmp_path):
        # Spec scenario (spec.md): program overrides org for one connection; the
        # program's definition wins in that program, while unrelated org-scope
        # connections still resolve.
        org = tmp_path / "org.yaml"
        program = tmp_path / "program.yaml"
        _write(
            org,
            [
                {"name": "deploy-target", "type": "ssh", "endpoint": "org-host", "scope": "org"},
                {"name": "shared-api", "type": "api", "endpoint": "api.ex.com", "scope": "org"},
            ],
        )
        _write(
            program,
            [
                {
                    "name": "deploy-target",
                    "type": "ssh",
                    "endpoint": "program-host",
                    "scope": "program",
                }
            ],
        )
        resolved = resolve([("org", org), ("program", program)])
        by_name = {c.name: c for c in resolved}
        # program wins for the overridden key...
        assert by_name["deploy-target"].endpoint == "program-host"
        assert by_name["deploy-target"].scope == "program"
        # ...and the unrelated org connection still resolves
        assert by_name["shared-api"].endpoint == "api.ex.com"
        assert by_name["shared-api"].scope == "org"

    def test_nearest_scope_wins_across_all_three(self, tmp_path):
        tenant = tmp_path / "tenant.yaml"
        org = tmp_path / "org.yaml"
        program = tmp_path / "program.yaml"
        _write(tenant, [{"name": "c", "type": "api", "endpoint": "tenant"}])
        _write(org, [{"name": "c", "type": "api", "endpoint": "org"}])
        _write(program, [{"name": "c", "type": "api", "endpoint": "program"}])
        resolved = resolve([("tenant", tenant), ("org", org), ("program", program)])
        assert len(resolved) == 1
        assert resolved[0].endpoint == "program"

    def test_missing_files_are_skipped(self, tmp_path):
        program = tmp_path / "program.yaml"
        _write(program, [{"name": "only", "type": "api", "endpoint": "here"}])
        resolved = resolve([("tenant", tmp_path / "absent.yaml"), ("program", program)])
        assert [c.name for c in resolved] == ["only"]

    def test_empty_returns_empty(self, tmp_path):
        assert resolve([("tenant", tmp_path / "nope.yaml")]) == []

    def test_result_sorted_by_name(self, tmp_path):
        program = tmp_path / "program.yaml"
        _write(
            program,
            [
                {"name": "zeta", "type": "api", "endpoint": "z"},
                {"name": "alpha", "type": "api", "endpoint": "a"},
            ],
        )
        assert [c.name for c in resolve([("program", program)])] == ["alpha", "zeta"]


class TestScopeFiles:
    def test_default_scope_files_order_and_locations(self, tmp_path):
        program_root = tmp_path / "program"
        org_dir = tmp_path / "orgcfg"
        home = tmp_path / "home"
        files = default_scope_files(program_root, org_config_dir=org_dir, home=home)
        assert [label for label, _ in files] == ["tenant", "org", "program"]
        assert files[0][1] == home / ".otaman" / "connections.yaml"
        assert files[1][1] == org_dir / "connections.yaml"
        assert files[2][1] == program_root / "connections.yaml"

    def test_org_scope_omitted_when_no_org_dir(self, tmp_path):
        files = default_scope_files(tmp_path / "program", home=tmp_path / "home")
        assert [label for label, _ in files] == ["tenant", "program"]

    def test_resolve_for_reads_standard_layout(self, tmp_path):
        home = tmp_path / "home"
        (home / ".otaman").mkdir(parents=True)
        program_root = tmp_path / "program"
        program_root.mkdir()
        _write(
            home / ".otaman" / "connections.yaml",
            [{"name": "tenant-conn", "type": "api", "endpoint": "t"}],
        )
        _write(
            program_root / "connections.yaml",
            [{"name": "prog-conn", "type": "api", "endpoint": "p"}],
        )
        resolved = resolve_for(program_root, home=home)
        assert [c.name for c in resolved] == ["prog-conn", "tenant-conn"]


class TestValuesNeverExposed:
    def test_only_ref_locators_surface(self, tmp_path):
        # A connection carries secret_ref / ssh_ref (locators) — never a value.
        program = tmp_path / "program.yaml"
        _write(
            program,
            [
                {
                    "name": "sunflowers-ssh",
                    "type": "ssh",
                    "endpoint": "sunflowers.example.com",
                    "ssh_ref": "sunflowers",
                }
            ],
        )
        (conn,) = resolve([("program", program)])
        fields = set(vars(conn))
        # 1.2 added `kind` and `ssh_scope` — still all locators/metadata, no value.
        assert fields == {
            "name",
            "type",
            "endpoint",
            "scope",
            "secret_ref",
            "ssh_ref",
            "kind",
            "ssh_scope",
        }
        # no attribute could hold a value — the model has none
        assert not any("value" in f or "secret_env" in f for f in fields)
        assert conn.secret_ref is None
        assert conn.ssh_ref == "sunflowers"


class TestInventory:
    def test_missing_secret_refs_flags_absent_keys(self):
        conns = [
            Connection("a-conn", "api", "a", "program", secret_ref="present-key"),
            Connection("b-conn", "api", "b", "program", secret_ref="absent-key"),
            Connection("c-conn", "ssh", "c", "program", ssh_ref="host-c"),  # no secret_ref
        ]
        # available_keys stands in for the secret backend's list_keys()
        missing = missing_secret_refs(conns, available_keys={"present-key", "other"})
        assert missing == ["b-conn"]

    def test_no_missing_when_all_present(self):
        conns = [Connection("a-conn", "api", "a", "program", secret_ref="k")]
        assert missing_secret_refs(conns, {"k"}) == []


class TestConnectionKind:
    """1.2 (Q8): connection records carry a `kind` credential type."""

    def test_kinds_are_the_five_ruled(self):
        assert KINDS == ("pat", "deploy-key", "api-key", "oauth", "ssh")

    def test_parses_each_valid_kind(self):
        for kind in KINDS:
            conns = parse_connections(
                {"connections": [{"name": "c", "type": "api", "endpoint": "e", "kind": kind}]}
            )
            assert conns[0].kind == kind

    def test_kind_optional_defaults_none(self):
        conns = parse_connections({"connections": [{"name": "c", "type": "api", "endpoint": "e"}]})
        assert conns[0].kind is None

    def test_invalid_kind_raises_naming_allowed(self):
        with pytest.raises(ConnectionsError) as exc:
            parse_connections(
                {"connections": [{"name": "c", "type": "api", "endpoint": "e", "kind": "token"}]}
            )
        assert "kind" in str(exc.value)
        assert "pat" in str(exc.value)


class TestSshHostPointer:
    """1.2: external-resource → ssh Host pointer + scope note."""

    def test_parses_ssh_ref_and_scope(self):
        conns = parse_connections(
            {
                "connections": [
                    {
                        "name": "client-prod",
                        "type": "ssh",
                        "endpoint": "client-prod.example.com",
                        "kind": "ssh",
                        "ssh_ref": "client-prod-deploy",
                        "ssh_scope": "prod deploy, read-only",
                    }
                ]
            }
        )
        c = conns[0]
        assert c.ssh_ref == "client-prod-deploy"
        assert c.ssh_scope == "prod deploy, read-only"
        assert c.kind == "ssh"

    def test_ssh_scope_optional(self):
        conns = parse_connections(
            {"connections": [{"name": "c", "type": "ssh", "endpoint": "e", "ssh_ref": "h"}]}
        )
        assert conns[0].ssh_scope is None

    def test_pointer_holds_no_key_path_or_value(self):
        # The pointer is a Host NAME only — never a key path or secret value.
        c = Connection("r", "ssh", "e", "program", ssh_ref="host-alias", ssh_scope="note")
        assert "/" not in (c.ssh_ref or "")  # not a filesystem path
