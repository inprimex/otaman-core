#!/usr/bin/env python3
"""GitLab adapter — implements ``git_host.GitHostAdapter`` for GitLab.

Works with both SaaS (``gitlab.com``) and self-hosted GitLab instances;
the API path ``/api/v4`` is stable across both.

GitLab uses different terminology from GitHub — merge requests (MRs)
instead of pull requests, notes instead of comments — but the shape
is similar enough that the adapter hides those differences. The
user-facing number is the ``iid`` (project-scoped internal ID), not
the global ``id``; callers pass the iid (what they see in the URL).

Authentication: ``PRIVATE-TOKEN: <PAT>`` header. Token needs at least
``read_api`` for reads, ``api`` for posting comments.

Scope:
  - List / get MRs
  - Find MR by source branch
  - Post an issue-level comment (MR note)
  - List existing comments on an MR

Project slug (``group/project`` or ``group/subgroup/project``) is URL-
encoded into the ``:id`` path segment per GitLab convention.
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


_DEFAULT_UA = "maestro-plugin"


class GitLabAdapter:
    """GitHost adapter for GitLab (SaaS + self-hosted)."""

    provider = "gitlab"

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
        self.per_page = 100

    # ----- base URL -------------------------------------------------------

    @property
    def api_base(self) -> str:
        return f"https://{self.host}/api/v4"

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
            "PRIVATE-TOKEN": self.token,
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
            raise GitHostError(f"GitLab API unreachable: {e}") from e

    def _get_json(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        status, body, headers = self._request("GET", path, params=params)
        if status != 200:
            raise self._http_error("GET", path, status, body)
        try:
            return (json.loads(body.decode("utf-8") or "null"), headers)
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"GitLab returned non-JSON body: {e}") from e

    def _post_json(
        self, path: str, *, body: dict[str, Any], expected_status: int = 201,
    ) -> Any:
        status, resp_body, _ = self._request("POST", path, body=body)
        if status != expected_status:
            raise self._http_error("POST", path, status, resp_body)
        try:
            return json.loads(resp_body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"GitLab returned non-JSON body: {e}") from e

    def _http_error(
        self, method: str, path: str, status: int, body: bytes,
    ) -> GitHostError:
        hint = ""
        try:
            data = json.loads(body.decode("utf-8") or "null")
            if isinstance(data, dict):
                msg = data.get("message") or data.get("error") or ""
                if msg and not isinstance(msg, str):
                    msg = json.dumps(msg)
                if msg:
                    hint = f" — {msg}"
        except (ValueError, UnicodeDecodeError):
            pass

        if status == 401:
            hint += " (token invalid / expired — regenerate in GitLab → Preferences → Access Tokens)"
        elif status == 403:
            hint += (
                " (token missing scope — `api` required to post notes, "
                "`read_api` for reads)"
            )
        elif status == 404:
            hint += (
                " (project slug not found OR token can't see this project; "
                "check the slug and the token's project access)"
            )

        return GitHostError(
            f"GitLab {method} {path} failed: HTTP {status}{hint}",
            status=status,
        )

    # ----- pagination -----------------------------------------------------

    def _paginate(
        self, path: str, *, params: dict[str, Any] | None = None,
    ) -> list[Any]:
        """GitLab paginates via Link header (same shape as GitHub)."""
        merged = dict(params or {})
        merged.setdefault("per_page", self.per_page)

        items: list[Any] = []
        current_path = path
        current_params: dict[str, Any] | None = merged

        for _ in range(20):
            data, headers = self._get_json(current_path, params=current_params)
            if not isinstance(data, list):
                raise GitHostError(
                    f"GitLab {current_path} expected a JSON array, "
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

    # ----- slug helpers ---------------------------------------------------

    @staticmethod
    def _project_id(slug: str) -> str:
        """GitLab projects are identified by URL-encoded full path.

        'group/project' → 'group%2Fproject'
        'group/subgroup/project' → 'group%2Fsubgroup%2Fproject'
        """
        slug = (slug or "").strip().strip("/")
        if "/" not in slug:
            raise ValueError(f"invalid slug {slug!r}; expected 'group/project'")
        return urllib.parse.quote(slug, safe="")

    # ----- MR ops ---------------------------------------------------------

    def list_open_prs(self, slug: str) -> list[PullRequest]:
        pid = self._project_id(slug)
        raw = self._paginate(
            f"/projects/{pid}/merge_requests",
            params={"state": "opened", "order_by": "created_at", "sort": "desc"},
        )
        return [_to_pr(item, slug) for item in raw]

    def get_pr(self, slug: str, number: int) -> PullRequest:
        pid = self._project_id(slug)
        data, _ = self._get_json(f"/projects/{pid}/merge_requests/{number}")
        if not isinstance(data, dict):
            raise GitHostError(
                f"GitLab /merge_requests/{number} returned {type(data).__name__}"
            )
        return _to_pr(data, slug)

    def get_pr_for_branch(
        self, slug: str, branch: str,
    ) -> PullRequest | None:
        pid = self._project_id(slug)
        data, _ = self._get_json(
            f"/projects/{pid}/merge_requests",
            params={
                "state": "opened",
                "source_branch": branch,
                "per_page": 10,
                "order_by": "created_at",
                "sort": "desc",
            },
        )
        if not isinstance(data, list) or not data:
            return None
        return _to_pr(data[0], slug)

    def post_comment(
        self, slug: str, pr_number: int, body: str,
    ) -> Comment:
        """Post a discussion note on an MR."""
        if not body.strip():
            raise ValueError("comment body must be non-empty")
        pid = self._project_id(slug)
        data = self._post_json(
            f"/projects/{pid}/merge_requests/{pr_number}/notes",
            body={"body": body},
        )
        if not isinstance(data, dict):
            raise GitHostError(
                f"GitLab note POST returned {type(data).__name__}"
            )
        return _to_comment(data, slug, pr_number, self.host)

    def list_comments(
        self, slug: str, pr_number: int,
    ) -> list[Comment]:
        pid = self._project_id(slug)
        raw = self._paginate(
            f"/projects/{pid}/merge_requests/{pr_number}/notes",
        )
        return [_to_comment(item, slug, pr_number, self.host) for item in raw]


# ---------------------------------------------------------------------------
# Payload → dataclass mappers


def _to_pr(raw: dict[str, Any], slug: str) -> PullRequest:
    """Map a GitLab MR payload to our cross-provider PullRequest.

    GitLab uses iid (project-scoped) as the user-facing number. State
    vocabulary: opened/closed/merged; we normalise 'opened' → 'open'
    so callers can do a flat comparison with the GitHub adapter.
    """
    author = raw.get("author") or {}
    state = str(raw.get("state") or "open").lower()
    if state == "opened":
        state = "open"
    return PullRequest(
        number=int(raw.get("iid") or 0),
        title=str(raw.get("title") or ""),
        state=state,
        author=str(author.get("username") or ""),
        head_ref=str(raw.get("source_branch") or ""),
        base_ref=str(raw.get("target_branch") or ""),
        head_sha=str(raw.get("sha") or ""),
        url=str(raw.get("web_url") or ""),
        body=str(raw.get("description") or ""),
        draft=bool(raw.get("draft") or raw.get("work_in_progress") or False),
        raw=raw,
    )


def _to_comment(
    raw: dict[str, Any], slug: str, pr_number: int, host: str,
) -> Comment:
    author = raw.get("author") or {}
    # GitLab notes don't carry a direct web URL for the individual note;
    # the MR URL + anchor is the closest equivalent.
    note_id = int(raw.get("id") or 0)
    url = f"https://{host}/{slug}/-/merge_requests/{pr_number}#note_{note_id}"
    return Comment(
        id=note_id,
        author=str(author.get("username") or ""),
        body=str(raw.get("body") or ""),
        created_at=str(raw.get("created_at") or ""),
        url=url,
        raw=raw,
    )


def _parse_link_next(link_header: str) -> str | None:
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
