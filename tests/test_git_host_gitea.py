"""Tests for otaman_core.git_host_gitea — Gitea/Forgejo adapter."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from otaman_core import git_host as gh
from otaman_core import git_host_gitea as ghgi

# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def adapter():
    return ghgi.GiteaAdapter(host="gitea.example.com", token="gtea_test")


@pytest.fixture
def forgejo_adapter():
    return ghgi.GiteaAdapter(host="codeberg.org", token="t", provider="forgejo")


def _mock_response(*, status=200, body=b"", headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        body = body.encode("utf-8")
    return status, body, headers or {}


def _pr_payload(**overrides):
    base = {
        "number": 7,
        "title": "Add feature",
        "state": "open",
        "draft": False,
        "merged": False,
        "body": "PR body",
        "html_url": "https://gitea.example.com/o/r/pulls/7",
        "user": {"login": "alice"},
        "head": {"ref": "feature/x", "sha": "deadbeef"},
        "base": {"ref": "main"},
    }
    base.update(overrides)
    return base


def _comment_payload(**overrides):
    base = {
        "id": 11,
        "body": "LGTM",
        "created_at": "2026-06-07T10:00:00Z",
        "html_url": "https://gitea.example.com/o/r/pulls/7#issuecomment-11",
        "user": {"login": "bob"},
    }
    base.update(overrides)
    return base


def _repo_payload(**overrides):
    base = {
        "name": "my-service",
        "owner": {"login": "inprimex"},
        "clone_url": "https://gitea.example.com/inprimex/my-service.git",
        "ssh_url": "git@gitea.example.com:inprimex/my-service.git",
        "html_url": "https://gitea.example.com/inprimex/my-service",
        "private": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Construction + URL derivation


class TestConstruction:
    def test_requires_host(self):
        with pytest.raises(ValueError):
            ghgi.GiteaAdapter(host="", token="t")

    def test_api_base(self, adapter):
        assert adapter.api_base == "https://gitea.example.com/api/v1"

    def test_forgejo_self_identifies(self, forgejo_adapter):
        assert forgejo_adapter.provider == "forgejo"

    def test_unknown_provider_falls_back_to_gitea(self):
        a = ghgi.GiteaAdapter(host="h", token="t", provider="weird")
        assert a.provider == "gitea"


# ---------------------------------------------------------------------------
# PR reads


class TestGetPr:
    def test_returns_mapped(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(body=_pr_payload())
            pr = adapter.get_pr("o/r", 7)
        assert pr.number == 7
        assert pr.title == "Add feature"
        assert pr.state == "open"
        assert pr.author == "alice"
        assert pr.head_ref == "feature/x"
        assert pr.base_ref == "main"
        assert pr.head_sha == "deadbeef"

    def test_merged_state(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                body=_pr_payload(state="closed", merged=True),
            )
            pr = adapter.get_pr("o/r", 7)
        assert pr.state == "merged"

    def test_404_raises(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=404,
                body={"message": "Not Found"},
            )
            with pytest.raises(gh.GitHostError):
                adapter.get_pr("o/r", 7)


class TestListOpenPrs:
    def test_single_page(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                body=[_pr_payload(number=1), _pr_payload(number=2)],
            )
            prs = adapter.list_open_prs("o/r")
        assert [p.number for p in prs] == [1, 2]


class TestGetPrForBranch:
    def test_finds_by_source_branch(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                body=[
                    _pr_payload(number=1, head={"ref": "other", "sha": "a"}),
                    _pr_payload(number=2, head={"ref": "feature/x", "sha": "b"}),
                ],
            )
            pr = adapter.get_pr_for_branch("o/r", "feature/x")
        assert pr is not None
        assert pr.number == 2

    def test_no_match_returns_none(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(body=[])
            assert adapter.get_pr_for_branch("o/r", "missing") is None


# ---------------------------------------------------------------------------
# Comments


class TestPostComment:
    def test_returns_mapped(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=201, body=_comment_payload())
            c = adapter.post_comment("o/r", 7, "LGTM")
        assert c.id == 11
        assert c.author == "bob"
        assert c.body == "LGTM"

    def test_empty_body_rejected(self, adapter):
        with pytest.raises(ValueError):
            adapter.post_comment("o/r", 7, "   ")


# ---------------------------------------------------------------------------
# Repo lifecycle


class TestCreateRepo:
    def test_user_repo(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=201, body=_repo_payload())
            info = adapter.create_repo("my-service", org=None, private=True)
        # Inspect the POST path
        call = m.call_args
        assert call.kwargs.get("body", {}) or call[1].get("body", {})
        # url path arg
        method_arg, path_arg = call.args[0], call.args[1]
        assert method_arg == "POST"
        assert path_arg == "/user/repos"
        # The mapped RepoInfo
        assert info.name == "my-service"
        assert info.owner == "inprimex"
        assert info.private is True
        assert info.clone_url.endswith("/inprimex/my-service.git")
        assert info.ssh_url.startswith("git@")

    def test_org_repo(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=201, body=_repo_payload())
            adapter.create_repo("my-service", org="inprimex", private=False)
        method_arg, path_arg = m.call_args.args[0], m.call_args.args[1]
        assert method_arg == "POST"
        assert path_arg == "/orgs/inprimex/repos"

    def test_empty_name_rejected(self, adapter):
        with pytest.raises(ValueError):
            adapter.create_repo("  ", org=None)

    def test_request_body_includes_private_and_description(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=201, body=_repo_payload())
            adapter.create_repo("r", org=None, private=True, description="hello")
        body = m.call_args.kwargs["body"]
        assert body["name"] == "r"
        assert body["private"] is True
        assert body["description"] == "hello"
        assert body["auto_init"] is False


class TestDeleteRepo:
    def test_happy_path(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=204, body=b"")
            adapter.delete_repo("inprimex", "my-service")
        method_arg, path_arg = m.call_args.args[0], m.call_args.args[1]
        assert method_arg == "DELETE"
        assert path_arg == "/repos/inprimex/my-service"

    def test_404_raises(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=404,
                body={"message": "Not Found"},
            )
            with pytest.raises(gh.GitHostError):
                adapter.delete_repo("o", "r")

    def test_empty_args_rejected(self, adapter):
        with pytest.raises(ValueError):
            adapter.delete_repo("", "r")
        with pytest.raises(ValueError):
            adapter.delete_repo("o", "")
