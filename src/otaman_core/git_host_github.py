#!/usr/bin/env python3
"""GitHub adapter — implements ``git_host.GitHostAdapter`` for GitHub.

Works with both SaaS (``github.com``) and GitHub Enterprise; the only
difference is the API base URL (``api.github.com`` vs ``<host>/api/v3``).

Scope (Phase 2):
  - List / get PRs
  - Look up the PR that has ``<branch>`` as its source
  - Post a plain (issue-level) comment on a PR
  - List existing comments on a PR

Out of scope for now:
  - Review comments on specific lines/diff hunks (different endpoint,
    lands in Phase 3 if we need inline review routing)
  - Creating / merging / closing PRs — observer agents only comment.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otaman_core.git_host import Comment, GitHostError, PullRequest


_DEFAULT_UA = "maestro-plugin"


class GitHubAdapter:
    """GitHost adapter for GitHub (SaaS + Enterprise)."""

    provider = "github"

    def __init__(
        self,
        *,
        host: str,
        token: str,
        user_agent: str = _DEFAULT_UA,
    ):
        self.host = host
        self.token = token
        self.user_agent = user_agent
        # Per-page limit for list endpoints. GitHub's default is 30,
        # max 100. We stay at 100 to minimise round-trips without
        # tripping secondary-rate-limit safeguards.
        self.per_page = 100

    # ----- base URL -------------------------------------------------------

    @property
    def api_base(self) -> str:
        if self.host == "github.com":
            return "https://api.github.com"
        # GitHub Enterprise puts the API under /api/v3.
        return f"https://{self.host}/api/v3"

    # ----- low-level HTTP -------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        """Single HTTP call. Returns (status, body, headers)."""
        url = self.api_base + path
        if params:
            # Keep ordering deterministic for tests.
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(params)}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra_headers:
            headers.update(extra_headers)

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            # 4xx / 5xx — caller decides via status code.
            return e.code, e.read() or b"", dict(e.headers or {})
        except (urllib.error.URLError, OSError) as e:
            raise GitHostError(f"GitHub API unreachable: {e}") from e

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expected_status: int = 200,
    ) -> tuple[Any, dict[str, str]]:
        status, body, headers = self._request("GET", path, params=params)
        if status != expected_status:
            raise self._http_error("GET", path, status, body)
        try:
            return (json.loads(body.decode("utf-8") or "null"), headers)
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"GitHub returned non-JSON body: {e}") from e

    def _post_json(
        self,
        path: str,
        *,
        body: dict[str, Any],
        expected_status: int = 201,
    ) -> Any:
        status, resp_body, _ = self._request("POST", path, body=body)
        if status != expected_status:
            raise self._http_error("POST", path, status, resp_body)
        try:
            return json.loads(resp_body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"GitHub returned non-JSON body: {e}") from e

    def _http_error(
        self, method: str, path: str, status: int, body: bytes,
    ) -> GitHostError:
        """Turn a non-success HTTP status into an actionable message.

        The GitHub error body usually has a ``message`` field; surfacing
        it directly lets users see 'Bad credentials' / 'Not Found' /
        'Requires permissions' without digging through logs.
        """
        hint = ""
        try:
            data = json.loads(body.decode("utf-8") or "null")
            if isinstance(data, dict):
                msg = data.get("message", "")
                if msg:
                    hint = f" — {msg}"
        except (ValueError, UnicodeDecodeError):
            pass

        if status == 401:
            hint += " (token invalid / expired — regenerate and re-add to secrets.env)"
        elif status == 403:
            hint += (
                " (token missing scope or rate-limited — for read ops you "
                "need at minimum `repo` on classic PATs or "
                "`Pull requests: Read` on fine-grained tokens)"
            )
        elif status == 404:
            hint += (
                " (check the slug is owner/repo AND the token can see "
                "this repo — private repos require explicit scope)"
            )

        return GitHostError(
            f"GitHub {method} {path} failed: HTTP {status}{hint}",
            status=status,
        )

    # ----- pagination helper ---------------------------------------------

    def _paginate(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Follow ``Link: rel="next"`` headers until exhausted.

        Caps at 20 pages (2000 items at per_page=100) to avoid runaway
        pulls on huge repos; if a real project needs more, the caller
        should filter server-side via ``params`` rather than pulling
        the world.
        """
        merged_params = dict(params or {})
        merged_params.setdefault("per_page", self.per_page)

        items: list[Any] = []
        current_path = path
        current_params: dict[str, Any] | None = merged_params

        for _ in range(20):
            data, headers = self._get_json(
                current_path, params=current_params,
            )
            if not isinstance(data, list):
                raise GitHostError(
                    f"GitHub {current_path} expected a JSON array, "
                    f"got {type(data).__name__}"
                )
            items.extend(data)

            next_url = _parse_link_next(headers.get("Link") or headers.get("link") or "")
            if not next_url:
                break
            # The `next` link is a full URL; strip api_base and pass
            # it back as a path to keep params=None safe.
            if next_url.startswith(self.api_base):
                current_path = next_url[len(self.api_base):]
            else:
                current_path = next_url
            current_params = None

        return items

    # ----- PR ops ---------------------------------------------------------

    def list_open_prs(self, slug: str) -> list[PullRequest]:
        owner, repo = _split_slug(slug)
        raw_items = self._paginate(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "sort": "created", "direction": "desc"},
        )
        return [_to_pr(item) for item in raw_items]

    def get_pr(self, slug: str, number: int) -> PullRequest:
        owner, repo = _split_slug(slug)
        data, _ = self._get_json(f"/repos/{owner}/{repo}/pulls/{number}")
        if not isinstance(data, dict):
            raise GitHostError(
                f"GitHub /pulls/{number} returned {type(data).__name__}"
            )
        return _to_pr(data)

    def get_pr_for_branch(
        self, slug: str, branch: str,
    ) -> PullRequest | None:
        """Find the open PR whose source branch is ``branch``.

        If more than one matches (edge case), returns the most recent.
        None if no open PR sources from that branch.
        """
        owner, repo = _split_slug(slug)
        # GitHub's `head` filter requires `<owner>:<branch>`; using just
        # the branch name matches any fork, which is usually NOT what
        # the caller means by "my branch".
        data, _ = self._get_json(
            f"/repos/{owner}/{repo}/pulls",
            params={
                "state": "open",
                "head": f"{owner}:{branch}",
                "per_page": 10,
                "sort": "created",
                "direction": "desc",
            },
        )
        if not isinstance(data, list) or not data:
            return None
        return _to_pr(data[0])

    def post_comment(
        self, slug: str, pr_number: int, body: str,
    ) -> Comment:
        """Post an issue-level comment on a PR.

        GitHub treats PRs as issues for comment purposes — use the
        issues endpoint for plain comments. Line-level review comments
        use a different endpoint (out of Phase 2 scope).
        """
        if not body.strip():
            raise ValueError("comment body must be non-empty")
        owner, repo = _split_slug(slug)
        data = self._post_json(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            body={"body": body},
        )
        if not isinstance(data, dict):
            raise GitHostError(
                f"GitHub comment POST returned {type(data).__name__}"
            )
        return _to_comment(data)

    def list_comments(
        self, slug: str, pr_number: int,
    ) -> list[Comment]:
        owner, repo = _split_slug(slug)
        raw_items = self._paginate(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
        )
        return [_to_comment(item) for item in raw_items]


# ---------------------------------------------------------------------------
# Helpers


def _split_slug(slug: str) -> tuple[str, str]:
    parts = slug.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid slug {slug!r}; expected 'owner/repo'")
    return parts[0], parts[1]


def _to_pr(raw: dict[str, Any]) -> PullRequest:
    user = raw.get("user") or {}
    head = raw.get("head") or {}
    base = raw.get("base") or {}
    merged_at = raw.get("merged_at")
    state = str(raw.get("state") or "open").lower()
    if merged_at:
        state = "merged"
    return PullRequest(
        number=int(raw.get("number") or 0),
        title=str(raw.get("title") or ""),
        state=state,
        author=str(user.get("login") or ""),
        head_ref=str(head.get("ref") or ""),
        base_ref=str(base.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        url=str(raw.get("html_url") or ""),
        body=str(raw.get("body") or ""),
        draft=bool(raw.get("draft") or False),
        raw=raw,
    )


def _to_comment(raw: dict[str, Any]) -> Comment:
    user = raw.get("user") or {}
    return Comment(
        id=int(raw.get("id") or 0),
        author=str(user.get("login") or ""),
        body=str(raw.get("body") or ""),
        created_at=str(raw.get("created_at") or ""),
        url=str(raw.get("html_url") or ""),
        raw=raw,
    )


def _parse_link_next(link_header: str) -> str | None:
    """Extract the ``rel="next"`` URL from a GitHub Link header.

    Example: ``<https://api.github.com/…?page=2>; rel="next", <…>; rel="last"``.
    Returns None if there's no next page.
    """
    if not link_header:
        return None
    for chunk in link_header.split(","):
        parts = chunk.strip().split(";")
        if len(parts) < 2:
            continue
        url_part = parts[0].strip().strip("<>")
        for attr in parts[1:]:
            attr = attr.strip()
            if attr in ('rel="next"', "rel=next"):
                return url_part
    return None
