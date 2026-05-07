"""Tests for scripts/git_host_github.py — GitHub adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


from otaman_core import git_host as gh
from otaman_core import git_host_github as ghgh


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def adapter():
    return ghgh.GitHubAdapter(host="github.com", token="ghp_test")


@pytest.fixture
def enterprise_adapter():
    return ghgh.GitHubAdapter(host="github.mycorp.io", token="ghp_test")


def _pr_payload(**overrides) -> dict:
    """Minimal GitHub PR JSON — matches what the real API returns."""
    base = {
        "number": 42,
        "title": "Add widget",
        "state": "open",
        "draft": False,
        "body": "This PR adds a widget.",
        "html_url": "https://github.com/octo/app/pull/42",
        "user": {"login": "octocat"},
        "head": {"ref": "feature/widget", "sha": "abc123"},
        "base": {"ref": "main"},
        "merged_at": None,
    }
    base.update(overrides)
    return base


def _comment_payload(**overrides) -> dict:
    base = {
        "id": 1001,
        "body": "LGTM",
        "created_at": "2026-04-25T12:00:00Z",
        "html_url": "https://github.com/octo/app/pull/42#issuecomment-1001",
        "user": {"login": "reviewer"},
    }
    base.update(overrides)
    return base


def _mock_response(*, status=200, body=b"", headers=None):
    """Return a (status, body_bytes, headers_dict) tuple for _request stubs."""
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        body = body.encode("utf-8")
    return status, body, headers or {}


# ---------------------------------------------------------------------------
# URL derivation


class TestApiBase:
    def test_saas(self, adapter):
        assert adapter.api_base == "https://api.github.com"

    def test_enterprise(self, enterprise_adapter):
        assert enterprise_adapter.api_base == "https://github.mycorp.io/api/v3"


# ---------------------------------------------------------------------------
# PR reads


class TestGetPr:
    def test_returns_mapped_pullrequest(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=_pr_payload())
            pr = adapter.get_pr("octo/app", 42)
        assert pr.number == 42
        assert pr.title == "Add widget"
        assert pr.state == "open"
        assert pr.author == "octocat"
        assert pr.head_ref == "feature/widget"
        assert pr.head_sha == "abc123"
        assert pr.base_ref == "main"
        assert pr.url.endswith("/pull/42")
        assert not pr.draft

    def test_merged_pr_normalised_state(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=_pr_payload(
                    state="closed",
                    merged_at="2026-04-25T10:00:00Z",
                ),
            )
            pr = adapter.get_pr("o/r", 1)
        assert pr.state == "merged"

    def test_404_raises_githosterror(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=404, body={"message": "Not Found"},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.get_pr("o/r", 999)
        assert "404" in str(ei.value)
        assert "Not Found" in str(ei.value)
        assert ei.value.status == 404

    def test_401_surfaces_token_hint(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=401, body={"message": "Bad credentials"},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.get_pr("o/r", 1)
        assert "token invalid" in str(ei.value).lower() \
            or "expired" in str(ei.value).lower()


class TestListOpenPrs:
    def test_single_page(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=[_pr_payload(number=1), _pr_payload(number=2, title="B")],
            )
            prs = adapter.list_open_prs("octo/app")
        assert [p.number for p in prs] == [1, 2]

    def test_follows_link_next(self, adapter):
        """Pagination: GitHub returns Link: rel=next until exhausted."""
        page1 = [_pr_payload(number=1)]
        page2 = [_pr_payload(number=2)]
        responses = [
            _mock_response(
                status=200, body=page1,
                headers={
                    "Link": (
                        '<https://api.github.com/repositories/0/pulls?page=2>; '
                        'rel="next", <…>; rel="last"'
                    ),
                },
            ),
            _mock_response(status=200, body=page2),
        ]
        with patch.object(adapter, "_request", side_effect=responses):
            prs = adapter.list_open_prs("octo/app")
        assert [p.number for p in prs] == [1, 2]

    def test_empty(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=[])
            assert adapter.list_open_prs("o/r") == []


class TestGetPrForBranch:
    def test_found(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200, body=[_pr_payload(number=7)],
            )
            pr = adapter.get_pr_for_branch("octo/app", "feature/xyz")
        assert pr is not None
        assert pr.number == 7

    def test_not_found_returns_none(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=[])
            assert adapter.get_pr_for_branch("octo/app", "stale-branch") is None

    def test_uses_owner_colon_branch_filter(self, adapter):
        """Head filter MUST be `owner:branch` to avoid matching forks."""
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=[])
            adapter.get_pr_for_branch("octo/app", "feature/x")
        # _request called once; inspect its params.
        call = m.call_args
        # Signature: _request(method, path, *, params=..., body=..., ...)
        assert call.args[0] == "GET"
        assert call.kwargs["params"]["head"] == "octo:feature/x"


# ---------------------------------------------------------------------------
# Comments


class TestPostComment:
    def test_success(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=201, body=_comment_payload(),
            )
            c = adapter.post_comment("o/r", 42, "LGTM")
        assert c.id == 1001
        assert c.author == "reviewer"
        assert c.body == "LGTM"

    def test_empty_body_raises(self, adapter):
        with pytest.raises(ValueError, match="non-empty"):
            adapter.post_comment("o/r", 1, "")
        with pytest.raises(ValueError, match="non-empty"):
            adapter.post_comment("o/r", 1, "   \n  ")

    def test_403_forbidden_surfaces_scope_hint(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=403, body={"message": "Resource not accessible"},
            )
            with pytest.raises(gh.GitHostError) as ei:
                adapter.post_comment("o/r", 1, "hi")
        assert "scope" in str(ei.value).lower() \
            or "rate-limited" in str(ei.value).lower()

    def test_sends_correct_path(self, adapter):
        """PR comments go to the /issues/ endpoint, not /pulls/."""
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=201, body=_comment_payload(),
            )
            adapter.post_comment("octo/app", 42, "hi")
        call = m.call_args
        assert call.args[0] == "POST"
        assert call.args[1] == "/repos/octo/app/issues/42/comments"


class TestListComments:
    def test_returns_all(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(
                status=200,
                body=[
                    _comment_payload(id=1, body="first"),
                    _comment_payload(id=2, body="second"),
                ],
            )
            comments = adapter.list_comments("o/r", 1)
        assert [c.id for c in comments] == [1, 2]
        assert [c.body for c in comments] == ["first", "second"]


# ---------------------------------------------------------------------------
# Network error handling


class TestNetworkErrors:
    def test_urlopen_failure_raises_githosterror(self, adapter):
        import urllib.error
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("dns")):
            with pytest.raises(gh.GitHostError, match="unreachable"):
                adapter.get_pr("o/r", 1)

    def test_non_json_body_raises(self, adapter):
        with patch.object(adapter, "_request") as m:
            m.return_value = _mock_response(status=200, body=b"<html>oops")
            with pytest.raises(gh.GitHostError, match="non-JSON"):
                adapter.get_pr("o/r", 1)


# ---------------------------------------------------------------------------
# Link header parser


class TestParseLinkNext:
    def test_extracts_next(self):
        header = (
            '<https://api.github.com/pulls?page=2>; rel="next", '
            '<https://api.github.com/pulls?page=5>; rel="last"'
        )
        assert ghgh._parse_link_next(header) == "https://api.github.com/pulls?page=2"

    def test_no_next_returns_none(self):
        header = '<https://api.github.com/pulls?page=5>; rel="last"'
        assert ghgh._parse_link_next(header) is None

    def test_empty_header(self):
        assert ghgh._parse_link_next("") is None
        assert ghgh._parse_link_next(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Slug parsing


class TestSplitSlug:
    def test_valid(self):
        assert ghgh._split_slug("octo/app") == ("octo", "app")

    @pytest.mark.parametrize("bad", ["", "nope", "/rep", "owner/", "a/b/c"])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError, match="slug"):
            ghgh._split_slug(bad)


# ---------------------------------------------------------------------------
# Factory integration


class TestFactory:
    def test_github_cfg_returns_github_adapter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAESTRO_GH_FACTORY_TEST", "ghp_x")
        cfg = gh.GitHostConfig.from_dict({
            "provider": "github",
            "token": "MAESTRO_GH_FACTORY_TEST",
        })
        adapter = gh.get_adapter(cfg, maestro_root=tmp_path)
        assert isinstance(adapter, ghgh.GitHubAdapter)
        assert adapter.host == "github.com"
        assert adapter.token == "ghp_x"

    def test_missing_token_raises_githosterror(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEFINITELY_NOT_SET_ANYWHERE", raising=False)
        cfg = gh.GitHostConfig.from_dict({
            "provider": "github",
            "token": "DEFINITELY_NOT_SET_ANYWHERE",
        })
        with pytest.raises(gh.GitHostError, match="could not be resolved"):
            gh.get_adapter(cfg, maestro_root=tmp_path)

    def test_unknown_provider_raises(self, tmp_path, monkeypatch):
        """Providers outside the supported four raise clearly."""
        monkeypatch.setenv("T", "x")
        cfg = gh.GitHostConfig.from_dict({
            "provider": "made-up-forge",
            "token": "T",
        })
        with pytest.raises(gh.GitHostError, match="unknown provider"):
            gh.get_adapter(cfg, maestro_root=tmp_path)
