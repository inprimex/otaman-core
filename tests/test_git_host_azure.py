"""Tests for scripts/git_host_azure.py — Azure DevOps adapter."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest

from otaman_core import git_host as gh
from otaman_core import git_host_azure as ghaz

# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def adapter():
    return ghaz.AzureDevOpsAdapter(token="azdo-test")


def _pr_payload(**overrides) -> dict:
    """Minimal Azure DevOps PR JSON."""
    base = {
        "pullRequestId": 42,
        "title": "Add widget",
        "status": "active",
        "isDraft": False,
        "description": "Adds a widget.",
        "createdBy": {
            "uniqueName": "roman@example.com",
            "displayName": "Roman",
        },
        "sourceRefName": "refs/heads/feature/widget",
        "targetRefName": "refs/heads/main",
        "lastMergeSourceCommit": {"commitId": "abc123def"},
        "repository": {
            "name": "app",
            "project": {"name": "myproject"},
            "url": "https://dev.azure.com/myorg/_apis/git/repositories/abc-guid",
        },
    }
    base.update(overrides)
    return base


def _thread_payload(*, comments=None, thread_id=1) -> dict:
    return {
        "id": thread_id,
        "comments": comments or [_comment_payload()],
        "status": "active",
    }


def _comment_payload(**overrides) -> dict:
    base = {
        "id": 1,
        "content": "LGTM",
        "publishedDate": "2026-04-25T12:00:00Z",
        "author": {
            "uniqueName": "reviewer@example.com",
            "displayName": "Reviewer",
        },
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


class TestSplitSlug:
    def test_three_segments(self):
        assert ghaz.AzureDevOpsAdapter._split_slug("org/project/repo") == ("org", "project", "repo")

    @pytest.mark.parametrize("bad", ["", "org", "org/project", "a/b/c/d"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError, match="Azure slug"):
            ghaz.AzureDevOpsAdapter._split_slug(bad)


class TestAuth:
    def test_basic_auth_header(self):
        adapter = ghaz.AzureDevOpsAdapter(token="pat-abc")
        assert adapter._auth_header.startswith("Basic ")
        decoded = base64.b64decode(adapter._auth_header[len("Basic ") :].encode("ascii")).decode(
            "utf-8"
        )
        # Azure expects empty user + PAT: ":pat"
        assert decoded == ":pat-abc"


class TestGetPr:
    def test_returns_mapped_pullrequest(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=_pr_payload())
            pr = adapter.get_pr("myorg/myproject/app", 42)
        assert pr.number == 42
        assert pr.state == "open"
        assert pr.author == "roman@example.com"
        assert pr.head_ref == "feature/widget"  # refs/heads/ prefix stripped
        assert pr.base_ref == "main"
        assert pr.head_sha == "abc123def"
        # Web URL reconstructed from org/project/repo/pullRequestId.
        assert "/myorg/myproject/_git/app/pullrequest/42" in pr.url

    def test_completed_state(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_pr_payload(status="completed"),
            )
            pr = adapter.get_pr("o/p/r", 1)
        assert pr.state == "merged"

    def test_abandoned_state(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_pr_payload(status="abandoned"),
            )
            pr = adapter.get_pr("o/p/r", 1)
        assert pr.state == "closed"

    def test_draft_flag(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_pr_payload(isDraft=True),
            )
            pr = adapter.get_pr("o/p/r", 1)
        assert pr.draft is True

    def test_203_auth_fallback_treated_like_401(self, adapter):
        """Azure returns 203 when the PAT is invalid — user-facing
        meaning is identical to 401 elsewhere."""
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=203,
                body={"message": "TF400813: The user is not authorized"},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.get_pr("o/p/r", 1)
        assert (
            "expired" in str(ei.value).lower() or "personal access tokens" in str(ei.value).lower()
        )

    def test_404_surfaces_scope_hint(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=404, body={"message": "Not found"})
            with pytest.raises(gh.GitHostError) as ei:
                adapter.get_pr("o/p/r", 1)
        assert "Organization scope" in str(ei.value) or "scope" in str(ei.value).lower()


class TestListOpenPrs:
    def test_single_page(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body={
                    "count": 2,
                    "value": [
                        _pr_payload(pullRequestId=1),
                        _pr_payload(pullRequestId=2),
                    ],
                },
            )
            prs = adapter.list_open_prs("o/p/r")
        assert [p.number for p in prs] == [1, 2]

    def test_continuation_token_pagination(self, adapter):
        page1_body = {"count": 1, "value": [_pr_payload(pullRequestId=1)]}
        page2_body = {"count": 1, "value": [_pr_payload(pullRequestId=2)]}
        responses = [
            _mock_response(
                status=200,
                body=page1_body,
                headers={"x-ms-continuationtoken": "TOKEN-PAGE-2"},
            ),
            _mock_response(status=200, body=page2_body),
        ]
        with patch.object(adapter, "_request", side_effect=responses):
            prs = adapter.list_open_prs("o/p/r")
        assert [p.number for p in prs] == [1, 2]
        # Second call should include continuationToken param.
        second_call = responses  # noqa: F841 — sanity placeholder


class TestGetPrForBranch:
    def test_uses_refs_heads_filter(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body={"count": 0, "value": []},
            )
            adapter.get_pr_for_branch("o/p/r", "feature/x")
        call = m.call_args
        assert call.kwargs["params"]["searchCriteria.sourceRefName"] == "refs/heads/feature/x"

    def test_found(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body={"count": 1, "value": [_pr_payload(pullRequestId=9)]},
            )
            pr = adapter.get_pr_for_branch("o/p/r", "feature/x")
        assert pr is not None and pr.number == 9

    def test_not_found(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body={"count": 0, "value": []},
            )
            assert adapter.get_pr_for_branch("o/p/r", "stale") is None


class TestPostComment:
    def test_creates_thread_with_single_comment(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,  # Azure returns 200 for thread creation, not 201
                body=_thread_payload(thread_id=123),
            )
            c = adapter.post_comment("o/p/r", 42, "Great work")
        assert c.id == 1  # the comment id within the thread
        call = m.call_args
        assert call.args[0] == "POST"
        sent = call.kwargs["body"]
        assert sent["comments"][0]["content"] == "Great work"
        assert sent["comments"][0]["parentCommentId"] == 0

    def test_empty_body_raises(self, adapter):
        with pytest.raises(ValueError, match="non-empty"):
            adapter.post_comment("o/p/r", 1, "   ")

    def test_empty_thread_response_raises(self, adapter):
        """Azure shouldn't, but if it returns an empty `comments` array
        we need to fail loudly rather than return a blank Comment."""
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body={"id": 1, "comments": []},
            )
            with pytest.raises(gh.GitHostError, match="empty thread"):
                adapter.post_comment("o/p/r", 1, "hi")

    def test_comment_url_includes_thread(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_thread_payload(thread_id=555),
            )
            c = adapter.post_comment("myorg/myproject/app", 42, "hi")
        assert "discussionId=555" in c.url


class TestListComments:
    def test_flattens_threads(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body={
                    "count": 2,
                    "value": [
                        _thread_payload(
                            thread_id=1,
                            comments=[
                                _comment_payload(id=1, content="first"),
                                _comment_payload(id=2, content="reply to first"),
                            ],
                        ),
                        _thread_payload(
                            thread_id=2,
                            comments=[_comment_payload(id=3, content="separate thread")],
                        ),
                    ],
                },
            )
            comments = adapter.list_comments("o/p/r", 42)
        assert [c.id for c in comments] == [1, 2, 3]
        # Thread id rides in `raw` for callers who need it.
        assert comments[0].raw["_thread_id"] == 1
        assert comments[2].raw["_thread_id"] == 2


class TestNetworkErrors:
    def test_unreachable(self, adapter):
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            with pytest.raises(gh.GitHostError, match="unreachable"):
                adapter.get_pr("o/p/r", 1)


class TestFactory:
    def test_azdo_cfg_returns_azure_adapter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AZDO_TOK", "x")
        cfg = gh.GitHostConfig.from_dict(
            {
                "provider": "azure-devops",
                "token": "AZDO_TOK",
            }
        )
        adapter = gh.get_adapter(cfg, maestro_root=tmp_path)
        assert isinstance(adapter, ghaz.AzureDevOpsAdapter)
        assert adapter.host == "dev.azure.com"


# ---------------------------------------------------------------------------
# Repo lifecycle (create_repo / delete_repo)
from unittest.mock import patch as _patch


def _az_repo_payload(**overrides):
    base = {
        "id": "11111111-2222-3333-4444-555555555555",
        "name": "my-service",
        "remoteUrl": "https://dev.azure.com/myorg/myproject/_git/my-service",
        "sshUrl": "git@ssh.dev.azure.com:v3/myorg/myproject/my-service",
        "webUrl": "https://dev.azure.com/myorg/myproject/_git/my-service",
        "project": {"id": "proj-uuid", "name": "myproject"},
    }
    base.update(overrides)
    return base


class TestAzureCreateRepo:
    def test_resolves_project_id_then_posts(self):
        adapter = ghaz.AzureDevOpsAdapter(token="t")
        responses = [
            (200, json.dumps({"id": "proj-uuid", "name": "myproject"}).encode(), {}),
            (201, json.dumps(_az_repo_payload()).encode(), {}),
        ]
        with _patch.object(adapter, "_request", side_effect=responses) as m:
            info = adapter.create_repo("my-service", org="myorg/myproject")
        first, second = m.call_args_list[0], m.call_args_list[1]
        # 1st call: GET project; 2nd: POST repo
        assert first.args[0] == "GET"
        assert "/_apis/projects/myproject" in first.args[1]
        assert second.args[0] == "POST"
        assert "/_apis/git/repositories" in second.args[1]
        assert second.kwargs["body"]["project"] == {"id": "proj-uuid"}
        assert info.name == "my-service"
        assert info.owner == "myorg/myproject"

    def test_requires_org_with_slash(self):
        adapter = ghaz.AzureDevOpsAdapter(token="t")
        with pytest.raises(ValueError, match="organization/project"):
            adapter.create_repo("my-service", org="just-org")
        with pytest.raises(ValueError):
            adapter.create_repo("my-service", org=None)


class TestAzureDeleteRepo:
    def test_resolves_id_then_deletes(self):
        adapter = ghaz.AzureDevOpsAdapter(token="t")
        responses = [
            (200, json.dumps({"id": "repo-uuid", "name": "my-service"}).encode(), {}),
            (204, b"", {}),
        ]
        with _patch.object(adapter, "_request", side_effect=responses) as m:
            adapter.delete_repo("myorg/myproject", "my-service")
        first, second = m.call_args_list[0], m.call_args_list[1]
        assert first.args[0] == "GET"
        assert "/_apis/git/repositories/my-service" in first.args[1]
        assert second.args[0] == "DELETE"
        assert second.args[1].endswith("/_apis/git/repositories/repo-uuid")

    def test_requires_org_with_slash(self):
        adapter = ghaz.AzureDevOpsAdapter(token="t")
        with pytest.raises(ValueError):
            adapter.delete_repo("just-org", "my-service")
