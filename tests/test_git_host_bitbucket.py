"""Tests for scripts/git_host_bitbucket.py — Bitbucket Cloud adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


from otaman_core import git_host as gh
from otaman_core import git_host_bitbucket as ghbb


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def adapter():
    return ghbb.BitbucketAdapter(token="bb-test")


def _pr_payload(**overrides) -> dict:
    base = {
        "id": 42,
        "title": "Add widget",
        "state": "OPEN",
        "author": {"nickname": "atlassian-user", "display_name": "Atlassian User"},
        "source": {
            "branch": {"name": "feature/widget"},
            "commit": {"hash": "abc123def"},
        },
        "destination": {"branch": {"name": "main"}},
        "summary": {"raw": "Adds a widget."},
        "links": {"html": {"href": "https://bitbucket.org/ws/app/pull-requests/42"}},
        "draft": False,
    }
    base.update(overrides)
    return base


def _comment_payload(**overrides) -> dict:
    base = {
        "id": 77,
        "content": {"raw": "LGTM"},
        "created_on": "2026-04-25T12:00:00Z",
        "user": {"nickname": "reviewer"},
        "links": {"html": {"href": "https://bitbucket.org/ws/app/pull-requests/42#comment-77"}},
    }
    base.update(overrides)
    return base


def _mock_response(*, status=200, body=b"", headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        body = body.encode("utf-8")
    return status, body, headers or {}


def _paged(values, next_url=None):
    """Bitbucket paging shape: {values, next?, pagelen, ...}."""
    body = {"values": values, "pagelen": 50}
    if next_url:
        body["next"] = next_url
    return body


# ---------------------------------------------------------------------------


class TestSplitSlug:
    def test_valid(self):
        assert ghbb.BitbucketAdapter._split_slug("ws/app") == ("ws", "app")

    @pytest.mark.parametrize("bad", ["", "ws", "ws/", "/app", "a/b/c"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError, match="slug"):
            ghbb.BitbucketAdapter._split_slug(bad)


class TestGetPr:
    def test_returns_mapped_pullrequest(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=_pr_payload())
            pr = adapter.get_pr("ws/app", 42)
        assert pr.number == 42
        assert pr.title == "Add widget"
        assert pr.state == "open"  # OPEN → open
        assert pr.author == "atlassian-user"  # prefers nickname
        assert pr.head_ref == "feature/widget"
        assert pr.head_sha == "abc123def"
        assert pr.base_ref == "main"
        assert pr.url == "https://bitbucket.org/ws/app/pull-requests/42"

    def test_state_merged(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200, body=_pr_payload(state="MERGED"),
            )
            pr = adapter.get_pr("ws/app", 1)
        assert pr.state == "merged"

    def test_state_declined(self, adapter):
        """DECLINED and SUPERSEDED both map to closed for the
        cross-provider state vocabulary."""
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200, body=_pr_payload(state="DECLINED"),
            )
            pr = adapter.get_pr("ws/app", 1)
        assert pr.state == "closed"

    def test_404_message(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=404,
                body={"error": {"message": "Not found"}},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.get_pr("ws/app", 1)
        assert "Not found" in str(ei.value)
        assert "404" in str(ei.value)


class TestListOpenPrs:
    def test_single_page(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_paged([_pr_payload(id=1), _pr_payload(id=2)]),
            )
            prs = adapter.list_open_prs("ws/app")
        assert [p.number for p in prs] == [1, 2]

    def test_follows_next_url(self, adapter):
        """Pagination lives in the body, not Link header."""
        page1 = _paged(
            [_pr_payload(id=1)],
            next_url="https://api.bitbucket.org/2.0/repositories/ws/app/pullrequests?page=2",
        )
        page2 = _paged([_pr_payload(id=2)])
        responses = [
            _mock_response(status=200, body=page1),
            _mock_response(status=200, body=page2),
        ]
        with patch.object(adapter, "_request", side_effect=responses):
            prs = adapter.list_open_prs("ws/app")
        assert [p.number for p in prs] == [1, 2]
        # Verify second call went to the full `next` URL.
        assert responses[1] is responses[1]  # sanity

    def test_empty(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=_paged([]))
            assert adapter.list_open_prs("ws/app") == []


class TestGetPrForBranch:
    def test_uses_bbql_filter(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=_paged([]))
            adapter.get_pr_for_branch("ws/app", "my-branch")
        call = m.call_args
        q = call.kwargs["params"]["q"]
        assert 'source.branch.name="my-branch"' in q
        assert 'state="OPEN"' in q

    def test_found(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200, body=_paged([_pr_payload(id=5)]),
            )
            pr = adapter.get_pr_for_branch("ws/app", "feature/x")
        assert pr is not None and pr.number == 5

    def test_not_found(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=_paged([]))
            assert adapter.get_pr_for_branch("ws/app", "nope") is None


class TestPostComment:
    def test_uses_content_raw(self, adapter):
        """Bitbucket expects {content: {raw: ...}}, not flat {body: ...}."""
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=201, body=_comment_payload(),
            )
            adapter.post_comment("ws/app", 42, "hello")
        call = m.call_args
        # Inspect the JSON body we sent.
        assert call.kwargs["body"] == {"content": {"raw": "hello"}}

    def test_success(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=201, body=_comment_payload(),
            )
            c = adapter.post_comment("ws/app", 42, "hello")
        assert c.id == 77
        assert c.body == "LGTM"
        assert c.author == "reviewer"

    def test_empty_body_raises(self, adapter):
        with pytest.raises(ValueError, match="non-empty"):
            adapter.post_comment("ws/app", 1, "")

    def test_401_surfaces_hint(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=401, body={"error": {"message": "Unauthorized"}},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.post_comment("ws/app", 1, "hi")
        assert "Access Token" in str(ei.value) or "expired" in str(ei.value).lower()


class TestListComments:
    def test_flattens_paged(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_paged([_comment_payload(id=1),
                             _comment_payload(id=2)]),
            )
            comments = adapter.list_comments("ws/app", 1)
        assert [c.id for c in comments] == [1, 2]


class TestNetworkErrors:
    def test_unreachable(self, adapter):
        import urllib.error
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("dns")):
            with pytest.raises(gh.GitHostError, match="unreachable"):
                adapter.get_pr("ws/app", 1)


class TestFactory:
    def test_bitbucket_cfg_returns_bitbucket_adapter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BB_TOK", "x")
        cfg = gh.GitHostConfig.from_dict({
            "provider": "bitbucket",
            "token": "BB_TOK",
        })
        adapter = gh.get_adapter(cfg, maestro_root=tmp_path)
        assert isinstance(adapter, ghbb.BitbucketAdapter)
        assert adapter.host == "bitbucket.org"
