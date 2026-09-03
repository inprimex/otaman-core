"""Tests for scripts/_secrets.py — tiered secret source resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# _secrets is provided by the otaman_core package (pyproject pythonpath = ["src"])
from otaman_core._secrets import (
    CREDENTIAL_LAYERS,
    DotenvSource,
    EnvSource,
    KeyringSource,
    SecretRef,
    credential_layer_paths,
    credential_provenance,
    list_keys,
    load_dotenv,
    org_config_secrets_path,
    register_source,
    resolve,
    resolve_cascade,
    resolve_or_fail,
    tenant_secrets_path,
    upsert_dotenv_secret,
)


@pytest.fixture
def maestro_root(tmp_path):
    """Create a maestro root with empty .maestro/ subdir."""
    root = tmp_path / "my-maestro"
    root.mkdir()
    (root / ".maestro").mkdir()
    return root


def _write_dotenv(root: Path, contents: str) -> None:
    (root / ".maestro" / "secrets.env").write_text(contents, encoding="utf-8")


class TestSecretRefFromConfig:
    def test_short_form_string(self):
        ref = SecretRef.from_config("MY_TOKEN")
        assert ref.sources == [{"type": "env", "name": "MY_TOKEN"}]

    def test_long_form_with_sources(self):
        cfg = {
            "sources": [
                {"type": "env", "name": "A"},
                {"type": "dotenv", "name": "B"},
            ]
        }
        ref = SecretRef.from_config(cfg)
        assert len(ref.sources) == 2
        assert ref.sources[0]["type"] == "env"
        assert ref.sources[1]["type"] == "dotenv"

    def test_single_source_dict(self):
        """Dict without 'sources' key treated as a single source."""
        ref = SecretRef.from_config({"type": "env", "name": "X"})
        assert ref.sources == [{"type": "env", "name": "X"}]

    def test_none_raises(self):
        with pytest.raises(ValueError):
            SecretRef.from_config(None)

    def test_bad_type_raises(self):
        with pytest.raises(ValueError):
            SecretRef.from_config(42)

    def test_sources_must_be_list(self):
        with pytest.raises(ValueError):
            SecretRef.from_config({"sources": "not a list"})


class TestEnvSource:
    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("FOO_TOKEN", "secret-value")
        ref = SecretRef.from_config("FOO_TOKEN")
        assert resolve(ref) == "secret-value"

    def test_missing_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("FOO_TOKEN", raising=False)
        ref = SecretRef.from_config("FOO_TOKEN")
        assert resolve(ref) is None

    def test_empty_env_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("FOO_TOKEN", "")
        ref = SecretRef.from_config("FOO_TOKEN")
        assert resolve(ref) is None

    def test_name_required(self):
        src = EnvSource()
        assert src.resolve({}, {}) is None


class TestDotenvSource:
    def test_reads_dotenv(self, maestro_root):
        _write_dotenv(maestro_root, "MY_KEY=dotenv-value\n")
        ref = SecretRef(sources=[{"type": "dotenv", "name": "MY_KEY"}])
        assert resolve(ref, maestro_root=maestro_root) == "dotenv-value"

    def test_comment_and_blank_lines_ignored(self, maestro_root):
        _write_dotenv(
            maestro_root,
            "# comment line\n\n   # indented comment\nMY_KEY=x\n",
        )
        ref = SecretRef(sources=[{"type": "dotenv", "name": "MY_KEY"}])
        assert resolve(ref, maestro_root=maestro_root) == "x"

    def test_double_quoted_value(self, maestro_root):
        _write_dotenv(maestro_root, 'MY_KEY="quoted value with spaces"\n')
        ref = SecretRef(sources=[{"type": "dotenv", "name": "MY_KEY"}])
        assert resolve(ref, maestro_root=maestro_root) == "quoted value with spaces"

    def test_single_quoted_value(self, maestro_root):
        _write_dotenv(maestro_root, "MY_KEY='single quoted'\n")
        ref = SecretRef(sources=[{"type": "dotenv", "name": "MY_KEY"}])
        assert resolve(ref, maestro_root=maestro_root) == "single quoted"

    def test_missing_key_returns_none(self, maestro_root):
        _write_dotenv(maestro_root, "OTHER=value\n")
        ref = SecretRef(sources=[{"type": "dotenv", "name": "MISSING"}])
        assert resolve(ref, maestro_root=maestro_root) is None

    def test_missing_file_returns_none(self, maestro_root):
        ref = SecretRef(sources=[{"type": "dotenv", "name": "X"}])
        assert resolve(ref, maestro_root=maestro_root) is None

    def test_no_maestro_root_returns_none(self):
        """DotenvSource needs a maestro_root in context."""
        src = DotenvSource()
        assert src.resolve({"name": "X"}, {}) is None

    def test_empty_value_treated_as_missing(self, maestro_root):
        _write_dotenv(maestro_root, "MY_KEY=\n")
        ref = SecretRef(sources=[{"type": "dotenv", "name": "MY_KEY"}])
        assert resolve(ref, maestro_root=maestro_root) is None


class TestSourceChain:
    def test_env_beats_dotenv(self, maestro_root, monkeypatch):
        """First non-empty value wins, in listed order."""
        monkeypatch.setenv("MY_KEY", "from-env")
        _write_dotenv(maestro_root, "MY_KEY=from-dotenv\n")
        ref = SecretRef(
            sources=[
                {"type": "env", "name": "MY_KEY"},
                {"type": "dotenv", "name": "MY_KEY"},
            ]
        )
        assert resolve(ref, maestro_root=maestro_root) == "from-env"

    def test_fallback_to_dotenv_when_env_missing(self, maestro_root, monkeypatch):
        monkeypatch.delenv("MY_KEY", raising=False)
        _write_dotenv(maestro_root, "MY_KEY=from-dotenv\n")
        ref = SecretRef(
            sources=[
                {"type": "env", "name": "MY_KEY"},
                {"type": "dotenv", "name": "MY_KEY"},
            ]
        )
        assert resolve(ref, maestro_root=maestro_root) == "from-dotenv"

    def test_unknown_source_type_skipped(self, maestro_root, monkeypatch):
        """Unknown types don't crash; chain continues."""
        monkeypatch.setenv("MY_KEY", "ok")
        ref = SecretRef(
            sources=[
                {"type": "vault", "path": "x"},  # unknown in v1
                {"type": "env", "name": "MY_KEY"},
            ]
        )
        assert resolve(ref, maestro_root=maestro_root) == "ok"

    def test_all_sources_fail_returns_none(self, maestro_root, monkeypatch):
        monkeypatch.delenv("MY_KEY", raising=False)
        ref = SecretRef(
            sources=[
                {"type": "env", "name": "MY_KEY"},
                {"type": "dotenv", "name": "MY_KEY"},
            ]
        )
        assert resolve(ref, maestro_root=maestro_root) is None


class TestResolveOrFail:
    def test_returns_value_when_found(self, monkeypatch):
        monkeypatch.setenv("X", "v")
        assert resolve_or_fail(SecretRef.from_config("X")) == "v"

    def test_raises_with_source_description(self, maestro_root, monkeypatch):
        monkeypatch.delenv("MISSING", raising=False)
        ref = SecretRef(
            sources=[
                {"type": "env", "name": "MISSING"},
                {"type": "dotenv", "name": "MISSING"},
                {"type": "keyring", "service": "maestro", "account": "x"},
            ]
        )
        with pytest.raises(RuntimeError) as exc:
            resolve_or_fail(ref, maestro_root=maestro_root)
        msg = str(exc.value)
        assert "env:MISSING" in msg
        assert "dotenv:MISSING" in msg
        assert "keyring:maestro/x" in msg


class TestLoadDotenv:
    def test_returns_all_pairs(self, maestro_root):
        _write_dotenv(
            maestro_root,
            '# header\nA=1\nB=two\nC="with spaces"\n',
        )
        result = load_dotenv(maestro_root)
        assert result == {"A": "1", "B": "two", "C": "with spaces"}

    def test_missing_file_returns_empty_dict(self, maestro_root):
        assert load_dotenv(maestro_root) == {}


class TestRegisterSource:
    def test_custom_source_plugs_in(self, monkeypatch):
        class StaticSource:
            type_name = "static-test"

            def resolve(self, spec, context):
                return spec.get("value")

        register_source(StaticSource())
        try:
            ref = SecretRef(sources=[{"type": "static-test", "value": "hello"}])
            assert resolve(ref) == "hello"
        finally:
            # Clean up registered source to avoid test pollution.
            from otaman_core._secrets import _BUILTIN_SOURCES

            _BUILTIN_SOURCES.pop("static-test", None)


class TestKeyringSource:
    def test_missing_keyring_package_returns_none(self, monkeypatch):
        """If keyring isn't importable, source silently yields None."""
        # Force ImportError by hiding the module.
        import importlib

        monkeypatch.setitem(sys.modules, "keyring", None)
        src = KeyringSource()
        assert src.resolve({"account": "x"}, {}) is None
        # Restore so other tests that may use keyring aren't broken.
        monkeypatch.delitem(sys.modules, "keyring", raising=False)
        importlib.invalidate_caches()

    def test_account_required(self):
        src = KeyringSource()
        assert src.resolve({"service": "maestro"}, {}) is None


class TestTenantDotenvScope:
    """`scope: tenant` reads ~/.otaman/secrets.env (hitl TOTP seed lives here)."""

    def test_tenant_path_is_home_otaman_secrets_env(self, tmp_path):
        assert tenant_secrets_path(tmp_path) == tmp_path / ".otaman" / "secrets.env"

    def test_resolves_tenant_scoped_ref(self, tmp_path):
        upsert_dotenv_secret(tenant_secrets_path(tmp_path), "HITL_TOTP_roman", "JBSWY3DPEHPK3PXP")
        ref = SecretRef.from_config(
            {"type": "dotenv", "name": "HITL_TOTP_roman", "scope": "tenant"}
        )
        assert resolve(ref, home=tmp_path) == "JBSWY3DPEHPK3PXP"

    def test_tenant_scope_does_not_read_workspace(self, tmp_path, maestro_root):
        # A tenant-scoped ref must NOT fall through to the workspace dotenv.
        (maestro_root / ".otaman").mkdir()
        (maestro_root / ".otaman" / "secrets.env").write_text("HITL_TOTP_roman=WORKSPACE\n")
        ref = SecretRef.from_config(
            {"type": "dotenv", "name": "HITL_TOTP_roman", "scope": "tenant"}
        )
        # tenant home (tmp_path) has no such file -> None, ignoring the workspace value
        assert resolve(ref, maestro_root=maestro_root, home=tmp_path) is None

    def test_workspace_scope_still_default(self, tmp_path, maestro_root):
        (maestro_root / ".otaman").mkdir()
        (maestro_root / ".otaman" / "secrets.env").write_text("KEY=ws-value\n")
        ref = SecretRef.from_config({"type": "dotenv", "name": "KEY"})  # no scope
        assert resolve(ref, maestro_root=maestro_root) == "ws-value"


class TestUpsertDotenvSecret:
    def test_creates_file_and_round_trips_via_reader(self, tmp_path):
        path = tenant_secrets_path(tmp_path)
        upsert_dotenv_secret(path, "HITL_TOTP_a", "ABC234")
        assert path.is_file()
        # round-trip through the tenant reader:
        ref = SecretRef.from_config({"type": "dotenv", "name": "HITL_TOTP_a", "scope": "tenant"})
        assert resolve(ref, home=tmp_path) == "ABC234"

    def test_updates_in_place_preserving_other_keys(self, tmp_path):
        path = tenant_secrets_path(tmp_path)
        upsert_dotenv_secret(path, "A", "1")
        upsert_dotenv_secret(path, "B", "2")
        upsert_dotenv_secret(path, "A", "updated")
        text = path.read_text()
        assert "A=updated" in text
        assert "B=2" in text
        assert text.count("A=") == 1  # replaced, not duplicated

    def test_preserves_comments_and_blanks(self, tmp_path):
        path = tenant_secrets_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("# my secrets\n\nEXISTING=keep\n")
        upsert_dotenv_secret(path, "NEW", "val")
        text = path.read_text()
        assert "# my secrets" in text and "EXISTING=keep" in text and "NEW=val" in text

    def test_quotes_values_needing_it(self, tmp_path):
        path = tenant_secrets_path(tmp_path)
        upsert_dotenv_secret(path, "SP", "a b")
        assert 'SP="a b"' in path.read_text()
        ref = SecretRef.from_config({"type": "dotenv", "name": "SP", "scope": "tenant"})
        assert resolve(ref, home=tmp_path) == "a b"  # reader unwraps

    def test_rejects_bad_key(self, tmp_path):
        path = tenant_secrets_path(tmp_path)
        for bad in ("", "has space", "has=eq"):
            with pytest.raises(ValueError):
                upsert_dotenv_secret(path, bad, "v")

    def test_rejects_unroundtrippable_value(self, tmp_path):
        path = tenant_secrets_path(tmp_path)
        with pytest.raises(ValueError):
            upsert_dotenv_secret(path, "K", "has\nnewline")
        with pytest.raises(ValueError):
            upsert_dotenv_secret(path, "K", 'has"quote')

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    def test_written_0600_dir_0700(self, tmp_path):
        path = tenant_secrets_path(tmp_path)
        upsert_dotenv_secret(path, "K", "v")
        assert (path.stat().st_mode & 0o777) == 0o600
        assert (path.parent.stat().st_mode & 0o777) == 0o700


class TestListKeys:
    """The values-free `list_keys()` seam that `otaman connection list` (cli 3.1)
    and `connections.missing_secret_refs` consume to badge backing keys."""

    def test_empty_when_no_stores(self, tmp_path):
        assert list_keys(home=tmp_path) == set()

    def test_enumerates_workspace_keys(self, tmp_path, maestro_root):
        (maestro_root / ".otaman").mkdir()
        (maestro_root / ".otaman" / "secrets.env").write_text("GH_PAT=x\nAPI_KEY=y\n# c\n")
        keys = list_keys(maestro_root=maestro_root, home=tmp_path)
        assert keys == {"GH_PAT", "API_KEY"}

    def test_enumerates_tenant_keys(self, tmp_path):
        upsert_dotenv_secret(tenant_secrets_path(tmp_path), "HITL_TOTP_roman", "seed")
        assert list_keys(home=tmp_path) == {"HITL_TOTP_roman"}

    def test_unions_workspace_and_tenant(self, tmp_path, maestro_root):
        (maestro_root / ".otaman").mkdir()
        (maestro_root / ".otaman" / "secrets.env").write_text("WS_KEY=1\n")
        upsert_dotenv_secret(tenant_secrets_path(tmp_path), "TENANT_KEY", "2")
        assert list_keys(maestro_root=maestro_root, home=tmp_path) == {"WS_KEY", "TENANT_KEY"}

    def test_returns_names_only_never_values(self, tmp_path):
        upsert_dotenv_secret(tenant_secrets_path(tmp_path), "SECRET_K", "s3cr3t-value")
        keys = list_keys(home=tmp_path)
        assert keys == {"SECRET_K"}
        assert "s3cr3t-value" not in keys  # values never surface

    def test_feeds_missing_secret_refs(self, tmp_path):
        # The real consumer flow: connection inventory badges refs w/o backing keys.
        from otaman_core.connections import Connection, missing_secret_refs

        upsert_dotenv_secret(tenant_secrets_path(tmp_path), "gh-pat", "x")
        conns = [
            Connection("has-key", "api", "a", "program", secret_ref="gh-pat"),
            Connection("no-key", "api", "b", "program", secret_ref="absent"),
        ]
        assert missing_secret_refs(conns, list_keys(home=tmp_path)) == ["no-key"]


def _write_secrets(path: Path, contents: str) -> None:
    """Write a secrets.env at an arbitrary layer path, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


class TestOrgDotenvScope:
    """`scope: org` reads ~/orgs/<org>/config/secrets.env — the middle layer."""

    def test_org_path_shape(self, tmp_path):
        assert (
            org_config_secrets_path("acme", tmp_path)
            == tmp_path / "orgs" / "acme" / "config" / "secrets.env"
        )

    def test_resolves_org_scoped_ref(self, tmp_path):
        _write_secrets(org_config_secrets_path("acme", tmp_path), "GITLAB_TOKEN=org-val\n")
        ref = SecretRef.from_config({"type": "dotenv", "name": "GITLAB_TOKEN", "scope": "org"})
        assert resolve(ref, org="acme", home=tmp_path) == "org-val"

    def test_org_scope_without_org_returns_none(self, tmp_path):
        _write_secrets(org_config_secrets_path("acme", tmp_path), "GITLAB_TOKEN=org-val\n")
        ref = SecretRef.from_config({"type": "dotenv", "name": "GITLAB_TOKEN", "scope": "org"})
        # no org supplied in context or spec -> cannot locate the org layer
        assert resolve(ref, home=tmp_path) is None

    def test_org_name_from_spec_when_absent_in_context(self, tmp_path):
        _write_secrets(org_config_secrets_path("acme", tmp_path), "K=spec-org\n")
        ref = SecretRef.from_config({"type": "dotenv", "name": "K", "scope": "org", "org": "acme"})
        assert resolve(ref, home=tmp_path) == "spec-org"


class TestCredentialCascade:
    """Per-key merge cascade program > org > tenant, nearest-scope-wins (1.1)."""

    def _layers(self, tmp_path, *, program=None, org=None, tenant=None):
        root = tmp_path / "prog"
        if program is not None:
            _write_secrets(root / ".otaman" / "secrets.env", program)
        if org is not None:
            _write_secrets(org_config_secrets_path("acme", tmp_path), org)
        if tenant is not None:
            _write_secrets(tenant_secrets_path(tmp_path), tenant)
        return root

    def test_layers_ordered_nearest_first(self):
        assert CREDENTIAL_LAYERS == ("program", "org", "tenant")

    def test_spec_scenario_program_over_org(self, tmp_path):
        # GIVEN GITHUB_TOKEN at org+program, GITLAB_TOKEN only at org
        root = self._layers(
            tmp_path,
            program="GITHUB_TOKEN=prog-gh\n",
            org="GITHUB_TOKEN=org-gh\nGITLAB_TOKEN=org-gl\n",
        )
        # THEN GITHUB_TOKEN resolves from program, GITLAB_TOKEN from org
        assert (
            resolve_cascade("GITHUB_TOKEN", maestro_root=root, org="acme", home=tmp_path)
            == "prog-gh"
        )
        assert (
            resolve_cascade("GITLAB_TOKEN", maestro_root=root, org="acme", home=tmp_path)
            == "org-gl"
        )

    def test_tenant_is_fallback_when_nearer_absent(self, tmp_path):
        root = self._layers(tmp_path, tenant="ONLY_TENANT=t\n")
        assert resolve_cascade("ONLY_TENANT", maestro_root=root, org="acme", home=tmp_path) == "t"

    def test_program_wins_over_tenant(self, tmp_path):
        root = self._layers(tmp_path, program="K=prog\n", tenant="K=tenant\n")
        assert resolve_cascade("K", maestro_root=root, org="acme", home=tmp_path) == "prog"

    def test_org_wins_over_tenant(self, tmp_path):
        root = self._layers(tmp_path, org="K=org\n", tenant="K=tenant\n")
        assert resolve_cascade("K", maestro_root=root, org="acme", home=tmp_path) == "org"

    def test_absent_key_returns_none(self, tmp_path):
        root = self._layers(tmp_path, program="A=1\n")
        assert resolve_cascade("MISSING", maestro_root=root, org="acme", home=tmp_path) is None

    def test_absent_layers_skipped_silently(self, tmp_path):
        # Only tenant supplied inputs (no maestro_root, no org) -> tenant-only cascade
        _write_secrets(tenant_secrets_path(tmp_path), "T=only\n")
        assert resolve_cascade("T", home=tmp_path) == "only"


class TestCredentialLayerPaths:
    def test_all_three_when_inputs_given(self, tmp_path):
        paths = credential_layer_paths(maestro_root=tmp_path / "prog", org="acme", home=tmp_path)
        assert list(paths) == ["program", "org", "tenant"]  # nearest-first
        assert paths["program"] == tmp_path / "prog" / ".otaman" / "secrets.env"
        assert paths["org"] == org_config_secrets_path("acme", tmp_path)
        assert paths["tenant"] == tenant_secrets_path(tmp_path)

    def test_program_omitted_without_maestro_root(self, tmp_path):
        paths = credential_layer_paths(org="acme", home=tmp_path)
        assert "program" not in paths
        assert set(paths) == {"org", "tenant"}

    def test_org_omitted_without_org(self, tmp_path):
        paths = credential_layer_paths(maestro_root=tmp_path / "prog", home=tmp_path)
        assert set(paths) == {"program", "tenant"}

    def test_paths_returned_even_when_files_absent(self, tmp_path):
        # location reporting must work whether or not the file exists
        paths = credential_layer_paths(maestro_root=tmp_path / "prog", org="acme", home=tmp_path)
        assert not paths["program"].exists()
        assert isinstance(paths["program"], Path)


class TestCredentialProvenance:
    """Values-free key -> winning-layer map for the discovery surfaces."""

    def test_maps_each_key_to_nearest_layer(self, tmp_path):
        root = tmp_path / "prog"
        _write_secrets(root / ".otaman" / "secrets.env", "GITHUB_TOKEN=x\n")
        _write_secrets(
            org_config_secrets_path("acme", tmp_path), "GITHUB_TOKEN=y\nGITLAB_TOKEN=z\n"
        )
        _write_secrets(tenant_secrets_path(tmp_path), "TENANT_ONLY=t\n")
        prov = credential_provenance(maestro_root=root, org="acme", home=tmp_path)
        assert prov == {
            "GITHUB_TOKEN": "program",  # program shadows org
            "GITLAB_TOKEN": "org",
            "TENANT_ONLY": "tenant",
        }

    def test_never_contains_values(self, tmp_path):
        _write_secrets(tenant_secrets_path(tmp_path), "SECRET_KEY=super-secret-value\n")
        prov = credential_provenance(home=tmp_path)
        assert "super-secret-value" not in prov.values()
        assert prov == {"SECRET_KEY": "tenant"}

    def test_empty_when_no_layers(self, tmp_path):
        assert credential_provenance(home=tmp_path) == {}


class TestListKeysOrgLayer:
    def test_unions_all_three_layers(self, tmp_path):
        root = tmp_path / "prog"
        _write_secrets(root / ".otaman" / "secrets.env", "P=1\n")
        _write_secrets(org_config_secrets_path("acme", tmp_path), "O=1\n")
        _write_secrets(tenant_secrets_path(tmp_path), "T=1\n")
        assert list_keys(maestro_root=root, org="acme", home=tmp_path) == {"P", "O", "T"}
