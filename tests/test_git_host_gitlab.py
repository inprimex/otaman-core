"""Tests for scripts/git_host_gitlab.py — GitLab adapter."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from otaman_core import git_host as gh
from otaman_core import git_host_gitlab as ghgl

# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def adapter():
    return ghgl.GitLabAdapter(host="gitlab.com", token="glpat-test")


@pytest.fixture
def self_hosted():
    return ghgl.GitLabAdapter(host="gitlab.mycorp.io", token="glpat-test")


def _mr_payload(**overrides) -> dict:
    base = {
        "iid": 42,
        "id": 99999,
        "title": "Add widget",
        "state": "opened",
        "draft": False,
        "description": "Adds a widget to the UI.",
        "web_url": "https://gitlab.com/group/project/-/merge_requests/42",
        "author": {"username": "tanuki"},
        "source_branch": "feature/widget",
        "target_branch": "main",
        "sha": "abc123def",
    }
    base.update(overrides)
    return base


def _note_payload(**overrides) -> dict:
    base = {
        "id": 777,
        "body": "LGTM",
        "created_at": "2026-04-25T12:00:00Z",
        "author": {"username": "reviewer"},
    }
    base.update(overrides)
    return base


def _mock_response(*, status=200, body=b"", headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        body = body.encode("utf-8")
    return status, body, headers or {}


# ---------------------------------------------------------------------------


class TestApiBase:
    def test_saas(self, adapter):
        assert adapter.api_base == "https://gitlab.com/api/v4"

    def test_self_hosted(self, self_hosted):
        assert self_hosted.api_base == "https://gitlab.mycorp.io/api/v4"


class TestProjectId:
    def test_simple(self):
        assert ghgl.GitLabAdapter._project_id("group/project") == "group%2Fproject"

    def test_nested_subgroup(self):
        assert ghgl.GitLabAdapter._project_id("group/sub/project") \
            == "group%2Fsub%2Fproject"

    def test_invalid(self):
        with pytest.raises(ValueError, match="slug"):
            ghgl.GitLabAdapter._project_id("no-slash")


class TestGetPr:
    def test_returns_mapped_pullrequest(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=_mr_payload())
            pr = adapter.get_pr("group/project", 42)
        assert pr.number == 42   # iid, not id
        assert pr.state == "open"  # normalized from "opened"
        assert pr.author == "tanuki"
        assert pr.head_ref == "feature/widget"
        assert pr.head_sha == "abc123def"

    def test_draft_via_work_in_progress(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_mr_payload(work_in_progress=True, draft=False),
            )
            pr = adapter.get_pr("g/p", 1)
        assert pr.draft is True

    def test_merged_state(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200, body=_mr_payload(state="merged"),
            )
            pr = adapter.get_pr("g/p", 1)
        assert pr.state == "merged"

    def test_401_surfaces_token_hint(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=401, body={"message": "401 Unauthorized"},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.get_pr("g/p", 1)
        assert "expired" in str(ei.value).lower() \
            or "access tokens" in str(ei.value).lower()

    def test_404_surfaces_project_hint(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=404, body={"message": "404 Project Not Found"},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.get_pr("g/p", 1)
        assert "project slug" in str(ei.value).lower() \
            or "token" in str(ei.value).lower()


class TestListOpenPrs:
    def test_single_page(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=[_mr_payload(iid=1), _mr_payload(iid=2, title="B")],
            )
            prs = adapter.list_open_prs("g/p")
        assert [p.number for p in prs] == [1, 2]

    def test_follows_link_next(self, adapter):
        responses = [
            _mock_response(
                status=200, body=[_mr_payload(iid=1)],
                headers={
                    "Link": '<https://gitlab.com/api/v4/projects/g%2Fp/merge_requests?page=2>; rel="next"',
                },
            ),
            _mock_response(status=200, body=[_mr_payload(iid=2)]),
        ]
        with patch.object(adapter, "_request", side_effect=responses):
            prs = adapter.list_open_prs("g/p")
        assert [p.number for p in prs] == [1, 2]

    def test_url_encoded_slug_in_path(self, adapter):
        """Project slug MUST be URL-encoded in the path."""
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=[])
            adapter.list_open_prs("group/sub/project")
        call = m.call_args
        assert call.args[0] == "GET"
        assert "group%2Fsub%2Fproject" in call.args[1]


class TestGetPrForBranch:
    def test_found(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200, body=[_mr_payload(iid=7)],
            )
            pr = adapter.get_pr_for_branch("g/p", "feature/xyz")
        assert pr is not None and pr.number == 7

    def test_not_found(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=[])
            assert adapter.get_pr_for_branch("g/p", "stale") is None

    def test_uses_source_branch_param(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=[])
            adapter.get_pr_for_branch("g/p", "feature/x")
        call = m.call_args
        assert call.kwargs["params"]["source_branch"] == "feature/x"


class TestPostComment:
    def test_success(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=201, body=_note_payload())
            c = adapter.post_comment("g/p", 42, "LGTM")
        assert c.id == 777
        assert c.author == "reviewer"
        assert c.body == "LGTM"
        # Note URL derived from host + slug + MR number + note id.
        assert "merge_requests/42" in c.url
        assert "note_777" in c.url

    def test_empty_body_raises(self, adapter):
        with pytest.raises(ValueError, match="non-empty"):
            adapter.post_comment("g/p", 1, "   ")

    def test_sends_correct_path(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=201, body=_note_payload())
            adapter.post_comment("group/project", 42, "hi")
        call = m.call_args
        assert call.args[0] == "POST"
        assert "/projects/group%2Fproject/merge_requests/42/notes" in call.args[1]

    def test_403_forbidden_surfaces_scope_hint(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=403, body={"message": "403 Forbidden"},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.post_comment("g/p", 1, "hi")
        assert "scope" in str(ei.value).lower()


class TestListComments:
    def test_returns_all(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=[_note_payload(id=1, body="first"),
                      _note_payload(id=2, body="second")],
            )
            comments = adapter.list_comments("g/p", 1)
        assert [c.id for c in comments] == [1, 2]


class TestNetworkErrors:
    def test_urlopen_failure(self, adapter):
        import urllib.error
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("dns")):
            with pytest.raises(gh.GitHostError, match="unreachable"):
                adapter.get_pr("g/p", 1)

    def test_non_json_body(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=b"<html>oops")
            with pytest.raises(gh.GitHostError, match="non-JSON"):
                adapter.get_pr("g/p", 1)


class TestFactory:
    def test_gitlab_cfg_returns_gitlab_adapter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAESTRO_GL_FACTORY_TEST", "glpat-x")
        cfg = gh.GitHostConfig.from_dict({
            "provider": "gitlab",
            "token": "MAESTRO_GL_FACTORY_TEST",
        })
        adapter = gh.get_adapter(cfg, maestro_root=tmp_path)
        assert isinstance(adapter, ghgl.GitLabAdapter)
        assert adapter.host == "gitlab.com"

    def test_self_hosted_host_flows_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("T", "x")
        cfg = gh.GitHostConfig.from_dict({
            "provider": "gitlab",
            "host": "gitlab.mycorp.io",
            "token": "T",
        })
        adapter = gh.get_adapter(cfg, maestro_root=tmp_path)
        assert adapter.host == "gitlab.mycorp.io"
        assert adapter.api_base == "https://gitlab.mycorp.io/api/v4"


# ---------------------------------------------------------------------------
# Repo lifecycle (create_repo / delete_repo)
from unittest.mock import patch as _patch


def _gl_repo_payload(**overrides):
    base = {
        "id": 99,
        "name": "my-service",
        "path": "my-service",
        "namespace": {"full_path": "mygroup", "path": "mygroup", "id": 17},
        "http_url_to_repo": "https://gitlab.com/mygroup/my-service.git",
        "ssh_url_to_repo": "git@gitlab.com:mygroup/my-service.git",
        "web_url": "https://gitlab.com/mygroup/my-service",
        "visibility": "private",
    }
    base.update(overrides)
    return base


class TestGitLabCreateRepo:
    def test_user_repo_no_namespace(self):
        adapter = ghgl.GitLabAdapter(host="gitlab.com", token="t")
        with _patch.object(adapter, "_request") as m:
            m.return_value = (201, json.dumps(_gl_repo_payload()).encode(), {})
            info = adapter.create_repo("my-service", org=None)
        method, path = m.call_args.args[0], m.call_args.args[1]
        assert method == "POST"
        assert path == "/projects"
        assert "namespace_id" not in m.call_args.kwargs["body"]
        assert info.name == "my-service"
        assert info.owner == "mygroup"
        assert info.private is True

    def test_org_repo_resolves_namespace(self):
        adapter = ghgl.GitLabAdapter(host="gitlab.com", token="t")
        responses = [
            (200, json.dumps({"id": 17, "full_path": "mygroup"}).encode(), {}),
            (201, json.dumps(_gl_repo_payload()).encode(), {}),
        ]
        with _patch.object(adapter, "_request", side_effect=responses) as m:
            adapter.create_repo("my-service", org="mygroup", private=False)
        # First call: GET /namespaces/mygroup; second: POST /projects
        first, second = m.call_args_list[0], m.call_args_list[1]
        assert first.args[0] == "GET"
        assert first.args[1] == "/namespaces/mygroup"
        assert second.args[0] == "POST"
        assert second.args[1] == "/projects"
        assert second.kwargs["body"]["namespace_id"] == 17
        assert second.kwargs["body"]["visibility"] == "public"

    def test_empty_name_rejected(self):
        adapter = ghgl.GitLabAdapter(host="gitlab.com", token="t")
        with pytest.raises(ValueError):
            adapter.create_repo("", org=None)


class TestGitLabDeleteRepo:
    def test_happy_path(self):
        adapter = ghgl.GitLabAdapter(host="gitlab.com", token="t")
        with _patch.object(adapter, "_request") as m:
            m.return_value = (202, b"", {})
            adapter.delete_repo("mygroup", "my-service")
        method, path = m.call_args.args[0], m.call_args.args[1]
        assert method == "DELETE"
        # Encoded "mygroup/my-service"
        assert path == "/projects/mygroup%2Fmy-service"

    def test_404_raises(self):
        adapter = ghgl.GitLabAdapter(host="gitlab.com", token="t")
        with _patch.object(adapter, "_request") as m:
            m.return_value = (404, json.dumps({"message": "Not Found"}).encode(), {})
            with pytest.raises(gh.GitHostError):
                adapter.delete_repo("o", "r")
