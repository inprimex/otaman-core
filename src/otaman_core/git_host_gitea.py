#!/usr/bin/env python3
"""Gitea / Forgejo adapter — implements ``git_host.GitHostAdapter``.

Covers both Gitea and Forgejo — Forgejo is a Gitea fork whose API v1
remains compatible. Both are always self-hosted; the ``host:`` field
in platform.yaml ``git_host:`` block is REQUIRED when ``provider:`` is
``gitea`` or ``forgejo``.

Authentication: ``Authorization: token <PAT>`` header (Gitea also
accepts Bearer, but ``token`` is the canonical scheme).

API shape is GitHub-flavoured (Gitea was designed to mirror GitHub
where possible), so this adapter looks a lot like ``git_host_github.py``.

Scope:
  - List / get PRs
  - Find PR by source branch
  - Post a plain (issue-level) comment on a PR
  - List existing comments on a PR
  - Create / delete repositories
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

from otaman_core.git_host import Comment, GitHostError, PullRequest, RepoInfo


_DEFAULT_UA = "otaman-plugin"


class GiteaAdapter:
    """GitHost adapter for Gitea / Forgejo (always self-hosted)."""

    provider = "gitea"

    def __init__(
        self,
        *,
        host: str,
        token: str,
        provider: str = "gitea",
        user_agent: str = _DEFAULT_UA,
    ):
        if not host:
            raise ValueError(
                "Gitea/Forgejo adapter requires explicit host (no SaaS default)"
            )
        self.host = host
        self.token = token
        # ``provider`` lets a Forgejo deployment self-identify in logs / messages
        # without changing API behaviour.
        self.provider = provider if provider in ("gitea", "forgejo") else "gitea"
        self.user_agent = user_agent
        self.per_page = 50

    # ----- base URL -------------------------------------------------------

    @property
    def api_base(self) -> str:
        return f"https://{self.host}/api/v1"

    # ----- low-level HTTP -------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        url = self.api_base + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {
            "Authorization": f"token {self.token}",
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
            raise GitHostError(f"Gitea/Forgejo API unreachable: {e}") from e

    def _get_json(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        status, body, headers = self._request("GET", path, params=params)
        if status != 200:
            raise self._http_error("GET", path, status, body)
        try:
            return (json.loads(body.decode("utf-8") or "null"), headers)
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"Gitea/Forgejo returned non-JSON body: {e}") from e

    def _post_json(
        self, path: str, *, body: dict[str, Any], expected_status: int = 201,
    ) -> Any:
        status, resp_body, _ = self._request("POST", path, body=body)
        if status != expected_status:
            raise self._http_error("POST", path, status, resp_body)
        try:
            return json.loads(resp_body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"Gitea/Forgejo returned non-JSON body: {e}") from e

    def _http_error(
        self, method: str, path: str, status: int, body: bytes,
    ) -> GitHostError:
        hint = ""
        try:
            data = json.loads(body.decode("utf-8") or "null")
            if isinstance(data, dict):
                msg = data.get("message") or data.get("errors") or ""
                if msg and not isinstance(msg, str):
                    msg = json.dumps(msg)
                if msg:
                    hint = f" — {msg}"
        except (ValueError, UnicodeDecodeError):
            pass

        if status == 401:
            hint += " (token invalid / expired — regenerate in Gitea/Forgejo Settings → Applications)"
        elif status == 403:
            hint += (
                " (token missing scope — write:repository or write:issue depending on the operation)"
            )
        elif status == 404:
            hint += (
                " (repo/PR not found OR token can't see it; "
                "check the slug and token's repo access)"
            )

        return GitHostError(
            f"{self.provider.title()} {method} {path} failed: HTTP {status}{hint}",
            status=status,
        )

    # ----- pagination -----------------------------------------------------

    def _paginate(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Gitea/Forgejo use Link headers like GitHub for pagination."""
        merged = dict(params or {})
        merged.setdefault("limit", self.per_page)

        items: list[Any] = []
        current_path = path
        current_params: dict[str, Any] | None = merged

        for _ in range(20):
            data, headers = self._get_json(current_path, params=current_params)
            if not isinstance(data, list):
                raise GitHostError(
                    f"{self.provider.title()} {current_path} expected a JSON array, "
                    f"got {type(data).__name__}"
                )
            items.extend(data)

            next_url = _parse_link_next(
                headers.get("Link") or headers.get("link") or ""
            )
            if not next_url:
                break
            if next_url.startswith(self.api_base):
                current_path = next_url[len(self.api_base):]
            else:
                current_path = next_url
            current_params = None

        return items

    # ----- slug -----------------------------------------------------------

    @staticmethod
    def _split_slug(slug: str) -> tuple[str, str]:
        parts = (slug or "").strip().split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"invalid slug {slug!r}; expected 'owner/repo'")
        return parts[0], parts[1]

    # ----- PR ops ---------------------------------------------------------

    def list_open_prs(self, slug: str) -> list[PullRequest]:
        owner, repo = self._split_slug(slug)
        raw = self._paginate(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "sort": "newest"},
        )
        return [_to_pr(item) for item in raw]

    def get_pr(self, slug: str, number: int) -> PullRequest:
        owner, repo = self._split_slug(slug)
        data, _ = self._get_json(f"/repos/{owner}/{repo}/pulls/{number}")
        if not isinstance(data, dict):
            raise GitHostError(
                f"{self.provider.title()} /pulls/{number} returned {type(data).__name__}"
            )
        return _to_pr(data)

    def get_pr_for_branch(
        self, slug: str, branch: str,
    ) -> PullRequest | None:
        """Gitea's /pulls doesn't filter by source branch; paginate + match.

        Caps at one page for the common case (most repos have <50 open PRs).
        """
        owner, repo = self._split_slug(slug)
        data, _ = self._get_json(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "sort": "newest", "limit": 50},
        )
        if not isinstance(data, list):
            return None
        for raw in data:
            head = raw.get("head") or {}
            if str(head.get("ref") or "") == branch:
                return _to_pr(raw)
        return None

    def post_comment(
        self, slug: str, pr_number: int, body: str,
    ) -> Comment:
        """Post an issue-level comment on a PR.

        Like GitHub, Gitea PRs are issues for comment purposes.
        """
        if not body.strip():
            raise ValueError("comment body must be non-empty")
        owner, repo = self._split_slug(slug)
        data = self._post_json(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            body={"body": body},
        )
        if not isinstance(data, dict):
            raise GitHostError(
                f"{self.provider.title()} comment POST returned {type(data).__name__}"
            )
        return _to_comment(data)

    def list_comments(
        self, slug: str, pr_number: int,
    ) -> list[Comment]:
        owner, repo = self._split_slug(slug)
        raw = self._paginate(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
        )
        return [_to_comment(item) for item in raw]

    # ----- repo lifecycle -------------------------------------------------

    def create_repo(
        self,
        name: str,
        org: str | None,
        private: bool = True,
        description: str = "",
    ) -> RepoInfo:
        """Create a remote repo. POST /user/repos or /orgs/{org}/repos."""
        if not name.strip():
            raise ValueError("repo name must be non-empty")
        path = f"/orgs/{org}/repos" if org else "/user/repos"
        body: dict[str, Any] = {
            "name": name,
            "private": private,
            "description": description or "",
            "auto_init": False,
        }
        data = self._post_json(path, body=body)
        if not isinstance(data, dict):
            raise GitHostError(
                f"{self.provider.title()} repo POST returned {type(data).__name__}"
            )
        return _to_repo_info(data)

    def delete_repo(self, owner: str, name: str) -> None:
        """Delete a remote repo. DELETE /repos/{owner}/{name}."""
        if not owner.strip() or not name.strip():
            raise ValueError("owner and name must be non-empty")
        path = f"/repos/{owner}/{name}"
        status, body, _ = self._request("DELETE", path)
        if status not in (204,):
            raise self._http_error("DELETE", path, status, body)


# ---------------------------------------------------------------------------
# Payload → dataclass mappers


def _to_pr(raw: dict[str, Any]) -> PullRequest:
    user = raw.get("user") or {}
    head = raw.get("head") or {}
    base = raw.get("base") or {}
    state = str(raw.get("state") or "open").lower()
    if raw.get("merged"):
        state = "merged"
    return PullRequest(
        number=int(raw.get("number") or 0),
        title=str(raw.get("title") or ""),
        state=state,
        author=str(user.get("login") or user.get("username") or ""),
        head_ref=str(head.get("ref") or ""),
        base_ref=str(base.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        url=str(raw.get("html_url") or raw.get("url") or ""),
        body=str(raw.get("body") or ""),
        draft=bool(raw.get("draft") or False),
        raw=raw,
    )


def _to_comment(raw: dict[str, Any]) -> Comment:
    user = raw.get("user") or {}
    return Comment(
        id=int(raw.get("id") or 0),
        author=str(user.get("login") or user.get("username") or ""),
        body=str(raw.get("body") or ""),
        created_at=str(raw.get("created_at") or ""),
        url=str(raw.get("html_url") or raw.get("url") or ""),
        raw=raw,
    )


def _to_repo_info(raw: dict[str, Any]) -> RepoInfo:
    owner_field = raw.get("owner") or {}
    return RepoInfo(
        name=str(raw.get("name") or ""),
        owner=str(owner_field.get("login") or owner_field.get("username") or ""),
        clone_url=str(raw.get("clone_url") or ""),
        ssh_url=str(raw.get("ssh_url") or ""),
        html_url=str(raw.get("html_url") or ""),
        private=bool(raw.get("private") or False),
    )


def _parse_link_next(link_header: str) -> str | None:
    """Extract the ``rel="next"`` URL from a Link header (same shape as GitHub)."""
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
