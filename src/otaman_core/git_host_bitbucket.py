#!/usr/bin/env python3
"""Bitbucket Cloud adapter — implements ``git_host.GitHostAdapter``.

Targets Bitbucket **Cloud** (``bitbucket.org``). Self-hosted Bitbucket
Data Center / Server uses a different REST base (``/rest/api/1.0``)
and payload shape; that's a separate adapter when someone needs it.

Authentication: Workspace / repository access tokens via
``Authorization: Bearer``. App passwords via Basic are possible but
deprecated — we support Bearer to stay on the future-proof path.

API quirks vs GitHub/GitLab:
  - Paging: ``{next: <url>}`` in the response body, NOT a Link header.
  - Users: nested as ``author.display_name`` + ``author.nickname``.
  - Comment bodies: ``{content: {raw: "..."}}``, not a flat ``body``.
  - PR state vocabulary: OPEN / MERGED / DECLINED / SUPERSEDED (uppercase).
  - Repo slug is ``workspace/repo``.

Scope:
  - List / get PRs
  - Find PR by source branch (via ``q=source.branch.name="..."``)
  - Post a PR comment
  - List PR comments
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otaman_core.git_host import Comment, GitHostError, PullRequest


_DEFAULT_UA = "otaman-plugin"
_API_BASE = "https://api.bitbucket.org/2.0"


class BitbucketAdapter:
    """GitHost adapter for Bitbucket Cloud."""

    provider = "bitbucket"

    def __init__(
        self,
        *,
        host: str = "bitbucket.org",
        token: str,
        user_agent: str = _DEFAULT_UA,
    ):
        self.host = host
        self.token = token
        self.user_agent = user_agent
        # pagelen is Bitbucket's per_page equivalent; max = 100.
        self.pagelen = 50

    @property
    def api_base(self) -> str:
        return _API_BASE

    # ----- low-level HTTP -------------------------------------------------

    def _request(
        self,
        method: str,
        url_or_path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        """Accepts either a path (``/repositories/...``) or a full URL.

        The full-URL case is used for pagination where Bitbucket hands
        back the next page as a complete URL in the response body.
        """
        if url_or_path.startswith("http"):
            url = url_or_path
        else:
            url = self.api_base + url_or_path
        if params:
            # urlencode handles the q= quote-inside-quote case correctly.
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read() or b"", dict(e.headers or {})
        except (urllib.error.URLError, OSError) as e:
            raise GitHostError(f"Bitbucket API unreachable: {e}") from e

    def _get_json(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> Any:
        status, body, _ = self._request("GET", path, params=params)
        if status != 200:
            raise self._http_error("GET", path, status, body)
        try:
            return json.loads(body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"Bitbucket returned non-JSON body: {e}") from e

    def _post_json(
        self, path: str, *, body: dict[str, Any], expected_status: int = 201,
    ) -> Any:
        status, resp_body, _ = self._request("POST", path, body=body)
        if status != expected_status:
            raise self._http_error("POST", path, status, resp_body)
        try:
            return json.loads(resp_body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"Bitbucket returned non-JSON body: {e}") from e

    def _http_error(
        self, method: str, path: str, status: int, body: bytes,
    ) -> GitHostError:
        hint = ""
        try:
            data = json.loads(body.decode("utf-8") or "null")
            if isinstance(data, dict):
                err = data.get("error") or {}
                if isinstance(err, dict):
                    msg = err.get("message", "")
                    if msg:
                        hint = f" — {msg}"
        except (ValueError, UnicodeDecodeError):
            pass

        if status == 401:
            hint += " (token invalid / expired — regenerate a Workspace or Repository Access Token)"
        elif status == 403:
            hint += (
                " (token missing scope — `pullrequest:write` required to "
                "post comments, `pullrequest` for reads)"
            )
        elif status == 404:
            hint += (
                " (workspace/repo not found OR token can't see it; "
                "check the slug and the token's workspace access)"
            )

        return GitHostError(
            f"Bitbucket {method} {path} failed: HTTP {status}{hint}",
            status=status,
        )

    # ----- pagination -----------------------------------------------------

    def _paginate(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Bitbucket pagination: body has ``values`` + optional ``next`` URL."""
        merged = dict(params or {})
        merged.setdefault("pagelen", self.pagelen)

        items: list[Any] = []
        current_path: str = path
        current_params: dict[str, Any] | None = merged

        for _ in range(20):
            data = self._get_json(current_path, params=current_params)
            if not isinstance(data, dict):
                raise GitHostError(
                    f"Bitbucket {current_path} expected a paged object, "
                    f"got {type(data).__name__}"
                )
            values = data.get("values") or []
            if not isinstance(values, list):
                raise GitHostError(
                    f"Bitbucket {current_path} values was "
                    f"{type(values).__name__}, expected list"
                )
            items.extend(values)
            next_url = data.get("next")
            if not next_url:
                break
            current_path = next_url
            current_params = None

        return items

    # ----- slug -----------------------------------------------------------

    @staticmethod
    def _split_slug(slug: str) -> tuple[str, str]:
        parts = (slug or "").strip().split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"invalid slug {slug!r}; expected 'workspace/repo'")
        return parts[0], parts[1]

    # ----- PR ops ---------------------------------------------------------

    def list_open_prs(self, slug: str) -> list[PullRequest]:
        workspace, repo = self._split_slug(slug)
        raw = self._paginate(
            f"/repositories/{workspace}/{repo}/pullrequests",
            params={"state": "OPEN"},
        )
        return [_to_pr(item) for item in raw]

    def get_pr(self, slug: str, number: int) -> PullRequest:
        workspace, repo = self._split_slug(slug)
        data = self._get_json(
            f"/repositories/{workspace}/{repo}/pullrequests/{number}"
        )
        if not isinstance(data, dict):
            raise GitHostError(
                f"Bitbucket /pullrequests/{number} returned {type(data).__name__}"
            )
        return _to_pr(data)

    def get_pr_for_branch(
        self, slug: str, branch: str,
    ) -> PullRequest | None:
        workspace, repo = self._split_slug(slug)
        # Bitbucket uses a BBQL filter in `q=`. source.branch.name must
        # be exact-matched with escaped quotes.
        q = f'state="OPEN" AND source.branch.name="{branch}"'
        data = self._get_json(
            f"/repositories/{workspace}/{repo}/pullrequests",
            params={"q": q, "pagelen": 10,
                    "sort": "-created_on"},
        )
        if not isinstance(data, dict):
            return None
        values = data.get("values") or []
        if not values:
            return None
        return _to_pr(values[0])

    def post_comment(
        self, slug: str, pr_number: int, body: str,
    ) -> Comment:
        if not body.strip():
            raise ValueError("comment body must be non-empty")
        workspace, repo = self._split_slug(slug)
        data = self._post_json(
            f"/repositories/{workspace}/{repo}/pullrequests/{pr_number}/comments",
            body={"content": {"raw": body}},
        )
        if not isinstance(data, dict):
            raise GitHostError(
                f"Bitbucket comment POST returned {type(data).__name__}"
            )
        return _to_comment(data)

    def list_comments(
        self, slug: str, pr_number: int,
    ) -> list[Comment]:
        workspace, repo = self._split_slug(slug)
        raw = self._paginate(
            f"/repositories/{workspace}/{repo}/pullrequests/{pr_number}/comments"
        )
        return [_to_comment(item) for item in raw]


# ---------------------------------------------------------------------------
# Payload → dataclass mappers


def _to_pr(raw: dict[str, Any]) -> PullRequest:
    author = raw.get("author") or {}
    # Source/destination nest branch info one layer deep.
    source = raw.get("source") or {}
    source_branch = (source.get("branch") or {})
    source_commit = (source.get("commit") or {})
    destination = raw.get("destination") or {}
    dest_branch = (destination.get("branch") or {})
    state_raw = str(raw.get("state") or "").upper()
    state_map = {
        "OPEN": "open",
        "MERGED": "merged",
        "DECLINED": "closed",
        "SUPERSEDED": "closed",
    }
    state = state_map.get(state_raw, state_raw.lower() or "open")
    links = raw.get("links") or {}
    html = (links.get("html") or {}).get("href", "")
    return PullRequest(
        number=int(raw.get("id") or 0),
        title=str(raw.get("title") or ""),
        state=state,
        author=str(author.get("nickname") or author.get("display_name") or ""),
        head_ref=str(source_branch.get("name") or ""),
        base_ref=str(dest_branch.get("name") or ""),
        head_sha=str(source_commit.get("hash") or ""),
        url=str(html or ""),
        body=str(raw.get("summary", {}).get("raw") or raw.get("description") or ""),
        draft=bool(raw.get("draft") or False),
        raw=raw,
    )


def _to_comment(raw: dict[str, Any]) -> Comment:
    user = raw.get("user") or {}
    content = raw.get("content") or {}
    links = raw.get("links") or {}
    html = (links.get("html") or {}).get("href", "")
    return Comment(
        id=int(raw.get("id") or 0),
        author=str(user.get("nickname") or user.get("display_name") or ""),
        body=str(content.get("raw") or ""),
        created_at=str(raw.get("created_on") or ""),
        url=str(html or ""),
        raw=raw,
    )
