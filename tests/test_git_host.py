"""Tests for scripts/git_host.py — URL parsing, config loading, validation."""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest


from otaman_core import git_host as gh


# ---------------------------------------------------------------------------
# parse_remote_url


class TestParseRemoteUrl:
    def test_github_ssh_legacy(self):
        info = gh.parse_remote_url("git@github.com:octocat/Hello-World.git")
        assert info.provider == "github"
        assert info.host == "github.com"
        assert info.owner == "octocat"
        assert info.repo == "Hello-World"
        assert info.slug == "octocat/Hello-World"
        assert not info.is_self_hosted

    def test_github_ssh_no_dotgit(self):
        info = gh.parse_remote_url("git@github.com:inprimex/maestro-plugin")
        assert info.provider == "github"
        assert info.repo == "maestro-plugin"

    def test_github_https(self):
        info = gh.parse_remote_url("https://github.com/torvalds/linux.git")
        assert info.provider == "github"
        assert info.host == "github.com"
        assert info.slug == "torvalds/linux"

    def test_github_https_with_token_user(self):
        info = gh.parse_remote_url("https://user:token@github.com/foo/bar.git")
        assert info.provider == "github"
        assert info.owner == "foo"
        assert info.repo == "bar"

    def test_gitlab_cloud(self):
        info = gh.parse_remote_url("git@gitlab.com:gitlab-org/gitlab.git")
        assert info.provider == "gitlab"
        assert info.host == "gitlab.com"
        assert not info.is_self_hosted

    def test_gitlab_self_hosted(self):
        info = gh.parse_remote_url("https://gitlab.mycorp.io/team/app.git")
        assert info.provider == "gitlab"  # host starts with gitlab.
        assert info.host == "gitlab.mycorp.io"
        assert info.is_self_hosted

    def test_bitbucket_cloud(self):
        info = gh.parse_remote_url("git@bitbucket.org:workspace/repo.git")
        assert info.provider == "bitbucket"
        assert info.slug == "workspace/repo"

    def test_azure_devops_https(self):
        info = gh.parse_remote_url(
            "https://dev.azure.com/myorg/myproject/_git/my-repo"
        )
        assert info.provider == "azure-devops"
        assert info.owner == "myorg/myproject"
        assert info.repo == "my-repo"

    def test_azure_devops_ssh(self):
        info = gh.parse_remote_url(
            "git@ssh.dev.azure.com:v3/myorg/myproject/my-repo"
        )
        # Azure SSH uses a different path shape than HTTPS — we only
        # guarantee correct classification for the HTTPS shape.
        assert info is not None
        assert info.host == "ssh.dev.azure.com"

    def test_unknown_self_hosted(self):
        info = gh.parse_remote_url("git@code.internal.example.com:team/app.git")
        assert info.provider == "unknown"
        assert info.is_self_hosted

    def test_garbage_returns_none(self):
        assert gh.parse_remote_url("") is None
        assert gh.parse_remote_url("not a url") is None

    def test_ssh_url_scheme(self):
        info = gh.parse_remote_url(
            "ssh://git@github.com/foo/bar.git"
        )
        assert info.provider == "github"
        assert info.slug == "foo/bar"


# ---------------------------------------------------------------------------
# detect_remotes_for_maestro


class TestDetectForMaestro:
    def test_no_platform_yaml(self, tmp_path):
        assert gh.detect_remotes_for_maestro(tmp_path) == []

    def test_skips_repos_without_git(self, tmp_path):
        (tmp_path / "platform.yaml").write_text(
            "project: test\nrepos:\n"
            "  - name: norepo\n    path: ../norepo\n",
            encoding="utf-8",
        )
        (tmp_path.parent / "norepo").mkdir(exist_ok=True)
        result = gh.detect_remotes_for_maestro(tmp_path)
        assert len(result) == 1
        assert result[0][1] is None


# ---------------------------------------------------------------------------
# GitHostConfig


class TestGitHostConfig:
    def test_from_dict_basic(self):
        cfg = gh.GitHostConfig.from_dict({
            "provider": "github",
            "token": {
                "sources": [{"type": "env", "name": "MAESTRO_GH_TOKEN"}],
            },
        })
        assert cfg.provider == "github"
        assert cfg.host == "github.com"  # default
        assert cfg.token_ref.sources == [
            {"type": "env", "name": "MAESTRO_GH_TOKEN"}
        ]

    def test_host_override(self):
        cfg = gh.GitHostConfig.from_dict({
            "provider": "gitlab",
            "host": "gitlab.mycorp.io",
            "token": "MAESTRO_GL_TOKEN",
        })
        assert cfg.host == "gitlab.mycorp.io"

    def test_token_short_form_env(self):
        cfg = gh.GitHostConfig.from_dict({
            "provider": "github",
            "token": "MAESTRO_GH_TOKEN",
        })
        assert cfg.token_ref.sources == [
            {"type": "env", "name": "MAESTRO_GH_TOKEN"}
        ]

    def test_missing_provider_raises(self):
        with pytest.raises(ValueError, match="provider"):
            gh.GitHostConfig.from_dict({"token": "T"})

    def test_missing_token_raises(self):
        with pytest.raises(ValueError, match="token"):
            gh.GitHostConfig.from_dict({"provider": "github"})


class TestLoadGitHostConfig:
    def test_no_yaml(self, tmp_path):
        assert gh.load_git_host_config(tmp_path) is None

    def test_no_block(self, tmp_path):
        (tmp_path / "platform.yaml").write_text(
            "project: test\nrepos: []\n", encoding="utf-8",
        )
        assert gh.load_git_host_config(tmp_path) is None

    def test_valid_block(self, tmp_path):
        (tmp_path / "platform.yaml").write_text(
            "project: test\n"
            "git_host:\n"
            "  provider: github\n"
            "  token:\n"
            "    sources:\n"
            "      - { type: env, name: GH_TOK }\n",
            encoding="utf-8",
        )
        cfg = gh.load_git_host_config(tmp_path)
        assert cfg is not None
        assert cfg.provider == "github"

    def test_malformed_returns_none(self, tmp_path):
        (tmp_path / "platform.yaml").write_text(
            "git_host:\n  provider: github\n", encoding="utf-8",
        )
        assert gh.load_git_host_config(tmp_path) is None  # missing token


# ---------------------------------------------------------------------------
# validate_token (mocked network)


class TestValidateToken:
    def test_github_ok(self):
        with patch.object(gh, "_do_get", return_value=(
            200, json.dumps({"login": "octocat"}).encode("utf-8"),
            {"X-OAuth-Scopes": "repo, read:user"},
        )):
            result = gh.validate_token("github", "github.com", "ghp_xxx")
        assert result.ok
        assert result.identity == "octocat"
        assert "repo" in (result.scopes or [])

    def test_github_401(self):
        with patch.object(gh, "_do_get", return_value=(401, b"{}", {})):
            result = gh.validate_token("github", "github.com", "bad")
        assert not result.ok
        assert "401" in result.error

    def test_gitlab_ok(self):
        with patch.object(gh, "_do_get", return_value=(
            200, json.dumps({"username": "tanuki"}).encode("utf-8"), {},
        )):
            result = gh.validate_token("gitlab", "gitlab.com", "glpat-xxx")
        assert result.ok
        assert result.identity == "tanuki"

    def test_bitbucket_ok(self):
        with patch.object(gh, "_do_get", return_value=(
            200, json.dumps({"username": "octocat"}).encode("utf-8"), {},
        )):
            result = gh.validate_token("bitbucket", "bitbucket.org", "xxx")
        assert result.ok

    def test_azure_ok(self):
        with patch.object(gh, "_do_get", return_value=(
            200,
            json.dumps({
                "authenticatedUser": {"providerDisplayName": "roman@example.com"}
            }).encode("utf-8"),
            {},
        )):
            result = gh.validate_token(
                "azure-devops", "dev.azure.com", "xxx",
            )
        assert result.ok
        assert "roman" in result.identity

    def test_unknown_provider(self):
        result = gh.validate_token("some-random-forge", "forge.local", "tok")
        assert not result.ok
        assert "unknown provider" in result.error

    def test_network_error(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("DNS lookup failed")
        with patch.object(gh, "_do_get", side_effect=boom):
            result = gh.validate_token("github", "github.com", "x")
        assert not result.ok
        assert "DNS" in result.error


# ---------------------------------------------------------------------------
# resolve_and_validate


class TestResolveAndValidate:
    def test_token_missing_from_sources(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_GH_TOKEN_FOR_TEST", raising=False)
        cfg = gh.GitHostConfig.from_dict({
            "provider": "github",
            "token": "NONEXISTENT_GH_TOKEN_FOR_TEST",
        })
        result = gh.resolve_and_validate(cfg, maestro_root=tmp_path)
        assert not result.ok
        assert "not found" in result.error

    def test_token_resolves_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAESTRO_GH_TOKEN_TEST", "ghp_fake")
        cfg = gh.GitHostConfig.from_dict({
            "provider": "github",
            "token": "MAESTRO_GH_TOKEN_TEST",
        })
        with patch.object(gh, "_do_get", return_value=(
            200, json.dumps({"login": "me"}).encode("utf-8"), {},
        )):
            result = gh.resolve_and_validate(cfg, maestro_root=tmp_path)
        assert result.ok
        assert result.identity == "me"
