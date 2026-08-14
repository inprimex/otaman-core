#!/usr/bin/env python3
"""Azure DevOps adapter — implements ``git_host.GitHostAdapter``.

Targets Azure DevOps Services (``dev.azure.com``). Azure DevOps
**Server** (on-prem) uses the same API shape under a different host,
so the adapter works there too if the host is set correctly.

The Azure model is three-tier: ``organization / project / repository``.
``git_host.parse_remote_url`` already captures that by putting
``org/project`` into ``owner`` — so the slug callers pass here is the
three-segment string ``org/project/repo``.

Authentication: HTTP Basic with empty username + PAT as password.
Base64'd into the Authorization header. Scope needed:
``Code (Read)`` for reads, ``Code (Read & Write)`` for comments.

API quirks vs GitHub/GitLab/Bitbucket:
  - Every request needs ``?api-version=7.1``.
  - PRs are queried under ``/git/repositories/{repoId}/pullrequests``
    scoped to ``/{org}/{project}`` in the URL prefix. We accept the
    repo **name** as the slug segment (Azure resolves by name OR id;
    we pass it through unchanged).
  - Comments are nested inside **threads**: a thread contains
    ordered comments. We model the top-level comment as a new thread,
    and ``list_comments`` flattens all comments out of all threads.
  - Pagination: ``x-ms-continuationtoken`` header; pass as
    ``?continuationToken=``.
  - Responses wrap list results in ``{count: N, value: [...]}``.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otaman_core.git_host import Comment, GitHostError, PullRequest, RepoInfo

_DEFAULT_UA = "otaman-plugin"
_API_VERSION = "7.1"


class AzureDevOpsAdapter:
    """GitHost adapter for Azure DevOps (Services + Server).

    Slug shape: ``organization/project/repository``.
    """

    provider = "azure-devops"

    def __init__(
        self,
        *,
        host: str = "dev.azure.com",
        token: str,
        user_agent: str = _DEFAULT_UA,
    ):
        self.host = host
        self.token = token
        self.user_agent = user_agent
        self._auth_header = self._build_auth(token)
        self.top = 100

    @staticmethod
    def _build_auth(token: str) -> str:
        raw = f":{token}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    # ----- slug + URL -----------------------------------------------------

    @staticmethod
    def _split_slug(slug: str) -> tuple[str, str, str]:
        parts = (slug or "").strip().split("/")
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"invalid Azure slug {slug!r}; expected 'organization/project/repository'"
            )
        return parts[0], parts[1], parts[2]

    def _project_base(self, slug: str) -> str:
        """URL prefix for a project-scoped API call."""
        org, project, _ = self._split_slug(slug)
        return f"https://{self.host}/{urllib.parse.quote(org)}/{urllib.parse.quote(project)}/_apis"

    # ----- low-level HTTP -------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        """``url`` is always a full URL — the base differs per slug so the
        caller builds it via ``_project_base``."""
        merged = dict(params or {})
        merged.setdefault("api-version", _API_VERSION)
        url = f"{url}?{urllib.parse.urlencode(merged)}"

        headers = {
            "Authorization": self._auth_header,
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
            raise GitHostError(f"Azure DevOps API unreachable: {e}") from e

    def _get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        status, body, headers = self._request("GET", url, params=params)
        if status != 200:
            raise self._http_error("GET", url, status, body)
        try:
            return (json.loads(body.decode("utf-8") or "null"), headers)
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"Azure DevOps returned non-JSON body: {e}") from e

    def _post_json(
        self,
        url: str,
        *,
        body: dict[str, Any],
        expected_status: int = 200,
    ) -> Any:
        # Azure DevOps returns 200 OK on thread creation (not 201).
        status, resp_body, _ = self._request("POST", url, body=body)
        if status != expected_status:
            raise self._http_error("POST", url, status, resp_body)
        try:
            return json.loads(resp_body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError) as e:
            raise GitHostError(f"Azure DevOps returned non-JSON body: {e}") from e

    def _http_error(
        self,
        method: str,
        url: str,
        status: int,
        body: bytes,
    ) -> GitHostError:
        hint = ""
        try:
            data = json.loads(body.decode("utf-8") or "null")
            if isinstance(data, dict):
                msg = data.get("message") or ""
                if msg:
                    hint = f" — {msg}"
        except (ValueError, UnicodeDecodeError):
            pass

        if status == 401 or status == 203:
            # Azure DevOps famously returns 203 Non-Authoritative (with
            # an HTML sign-in page) when the PAT is invalid/expired —
            # same user-facing meaning as 401 elsewhere.
            hint += " (token invalid / expired — regenerate a Personal Access Token in User settings → Personal access tokens)"  # noqa: E501
        elif status == 403:
            hint += (
                " (token missing scope — `Code (Read)` for reads, "
                "`Code (Read & Write)` for comments)"
            )
        elif status == 404:
            hint += (
                " (org/project/repo not found OR token scope doesn't cover it; "
                "check the slug and the PAT's Organization scope)"
            )

        # Shorten the URL in the message — the project base is long.
        short = url.replace(f"https://{self.host}/", "")
        return GitHostError(
            f"Azure DevOps {method} {short} failed: HTTP {status}{hint}",
            status=status,
        )

    # ----- pagination -----------------------------------------------------

    def _paginate(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Follow ``x-ms-continuationtoken`` headers until exhausted."""
        merged = dict(params or {})
        merged.setdefault("$top", self.top)

        items: list[Any] = []
        current_params = merged

        for _ in range(20):
            data, headers = self._get_json(url, params=current_params)
            if not isinstance(data, dict):
                raise GitHostError(
                    f"Azure DevOps {url} expected paged object, got {type(data).__name__}"
                )
            values = data.get("value") or []
            if not isinstance(values, list):
                raise GitHostError(f"Azure DevOps {url} value was {type(values).__name__}")
            items.extend(values)
            cont = headers.get("x-ms-continuationtoken") or headers.get("X-MS-ContinuationToken")
            if not cont:
                break
            current_params = dict(merged)
            current_params["continuationToken"] = cont

        return items

    # ----- PR ops ---------------------------------------------------------

    def list_open_prs(self, slug: str) -> list[PullRequest]:
        _, _, repo = self._split_slug(slug)
        base = self._project_base(slug)
        url = f"{base}/git/repositories/{urllib.parse.quote(repo)}/pullrequests"
        raw = self._paginate(
            url,
            params={"searchCriteria.status": "active"},
        )
        return [_to_pr(item, self.host) for item in raw]

    def get_pr(self, slug: str, number: int) -> PullRequest:
        _, _, repo = self._split_slug(slug)
        base = self._project_base(slug)
        url = f"{base}/git/repositories/{urllib.parse.quote(repo)}/pullrequests/{number}"
        data, _ = self._get_json(url)
        if not isinstance(data, dict):
            raise GitHostError(
                f"Azure DevOps /pullrequests/{number} returned {type(data).__name__}"
            )
        return _to_pr(data, self.host)

    def get_pr_for_branch(
        self,
        slug: str,
        branch: str,
    ) -> PullRequest | None:
        _, _, repo = self._split_slug(slug)
        base = self._project_base(slug)
        url = f"{base}/git/repositories/{urllib.parse.quote(repo)}/pullrequests"
        data, _ = self._get_json(
            url,
            params={
                "searchCriteria.status": "active",
                "searchCriteria.sourceRefName": f"refs/heads/{branch}",
                "$top": 10,
            },
        )
        if not isinstance(data, dict):
            return None
        values = data.get("value") or []
        if not values:
            return None
        return _to_pr(values[0], self.host)

    def post_comment(
        self,
        slug: str,
        pr_number: int,
        body: str,
    ) -> Comment:
        """Create a thread with a single comment — Azure's equivalent
        of an issue-level PR comment."""
        if not body.strip():
            raise ValueError("comment body must be non-empty")
        _, _, repo = self._split_slug(slug)
        base = self._project_base(slug)
        url = f"{base}/git/repositories/{urllib.parse.quote(repo)}/pullRequests/{pr_number}/threads"
        data = self._post_json(
            url,
            body={
                "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
                # 1 = active; we don't auto-resolve on post.
                "status": 1,
            },
        )
        if not isinstance(data, dict):
            raise GitHostError(f"Azure DevOps thread POST returned {type(data).__name__}")
        # Flatten to our Comment shape — take the first (and only)
        # comment from the created thread.
        comments = data.get("comments") or []
        if not comments:
            raise GitHostError("Azure DevOps returned an empty thread; comment may not have posted")
        first = comments[0]
        return _to_comment(first, slug, pr_number, data.get("id"), self.host)

    def list_comments(
        self,
        slug: str,
        pr_number: int,
    ) -> list[Comment]:
        """Flatten every comment from every thread on the PR.

        Azure groups comments into threads; a plain list view is what
        the Protocol exposes, so we flatten. The thread id rides along
        in ``Comment.raw['_thread_id']`` for callers who need it.
        """
        _, _, repo = self._split_slug(slug)
        base = self._project_base(slug)
        url = f"{base}/git/repositories/{urllib.parse.quote(repo)}/pullRequests/{pr_number}/threads"
        data, _ = self._get_json(url)
        if not isinstance(data, dict):
            return []
        threads = data.get("value") or []
        out: list[Comment] = []
        for thread in threads:
            thread_id = thread.get("id")
            for c in thread.get("comments") or []:
                out.append(_to_comment(c, slug, pr_number, thread_id, self.host))
        return out

    # ----- repo lifecycle -------------------------------------------------

    def create_repo(
        self,
        name: str,
        org: str | None,
        private: bool = True,
        description: str = "",
    ) -> RepoInfo:
        """Create a remote repo. POST /{org}/{project}/_apis/git/repositories.

        Azure's three-tier model means ``org`` here MUST be ``"organization/project"``.
        Private/public visibility is configured at project level on Azure DevOps,
        not per-repo — the ``private`` flag is accepted but does not control
        visibility (it's reflected on the returned ``RepoInfo`` for symmetry).
        ``description`` is accepted but Azure DevOps does not expose a per-repo
        description field via this API; the value is ignored.
        """
        if not name.strip():
            raise ValueError("repo name must be non-empty")
        if not org or "/" not in org:
            raise ValueError("Azure DevOps requires --org in 'organization/project' form")
        org_name, project = org.split("/", 1)
        project_id = self._resolve_project_id(org_name, project)
        url = (
            f"https://{self.host}/{urllib.parse.quote(org_name)}"
            f"/{urllib.parse.quote(project)}/_apis/git/repositories"
        )
        body: dict[str, Any] = {
            "name": name,
            "project": {"id": project_id},
        }
        data = self._post_json(url, body=body, expected_status=201)
        if not isinstance(data, dict):
            raise GitHostError(f"Azure DevOps repo POST returned {type(data).__name__}")
        return _to_repo_info(data, self.host, org_name, project, private)

    def delete_repo(self, owner: str, name: str) -> None:
        """Delete a remote repo. DELETE /{org}/{project}/_apis/git/repositories/{id}.

        ``owner`` MUST be ``"organization/project"``.
        """
        if not owner or "/" not in owner:
            raise ValueError(
                "Azure DevOps delete_repo requires owner in 'organization/project' form"
            )
        if not name.strip():
            raise ValueError("name must be non-empty")
        org_name, project = owner.split("/", 1)
        repo_id = self._resolve_repo_id(org_name, project, name)
        url = (
            f"https://{self.host}/{urllib.parse.quote(org_name)}"
            f"/{urllib.parse.quote(project)}/_apis/git/repositories/{repo_id}"
        )
        status, body, _ = self._request("DELETE", url)
        if status not in (200, 204):
            raise self._http_error("DELETE", url, status, body)

    def _resolve_project_id(self, org_name: str, project: str) -> str:
        """GET /{org}/_apis/projects/{project} → project.id (UUID)."""
        url = (
            f"https://{self.host}/{urllib.parse.quote(org_name)}"
            f"/_apis/projects/{urllib.parse.quote(project)}"
        )
        data, _ = self._get_json(url)
        if not isinstance(data, dict) or "id" not in data:
            raise GitHostError(f"Azure DevOps project {org_name}/{project} not found")
        return str(data["id"])

    def _resolve_repo_id(self, org_name: str, project: str, name: str) -> str:
        """GET /{org}/{project}/_apis/git/repositories/{name} → repo.id."""
        url = (
            f"https://{self.host}/{urllib.parse.quote(org_name)}"
            f"/{urllib.parse.quote(project)}/_apis/git/repositories/{urllib.parse.quote(name)}"
        )
        data, _ = self._get_json(url)
        if not isinstance(data, dict) or "id" not in data:
            raise GitHostError(f"Azure DevOps repo {org_name}/{project}/{name} not found")
        return str(data["id"])


# ---------------------------------------------------------------------------
# Payload → dataclass mappers


_AZDO_STATUS_TO_STATE = {
    "active": "open",
    "completed": "merged",
    "abandoned": "closed",
}


def _to_pr(raw: dict[str, Any], host: str) -> PullRequest:
    created_by = raw.get("createdBy") or {}
    status = str(raw.get("status") or "").lower()
    state = _AZDO_STATUS_TO_STATE.get(status, status or "open")
    # Azure names branches as `refs/heads/<name>` — strip to match GitHub.
    source_ref = str(raw.get("sourceRefName") or "")
    target_ref = str(raw.get("targetRefName") or "")
    head_ref = source_ref.removeprefix("refs/heads/") or source_ref
    base_ref = target_ref.removeprefix("refs/heads/") or target_ref
    # Azure PRs have a `url` to the API, not the web — construct the
    # human URL from org/project/repo/pullrequestId.
    repo = raw.get("repository") or {}
    project = (repo.get("project") or {}).get("name", "")
    org_url = str(repo.get("url") or "")
    repo_name = repo.get("name", "")
    pr_id = raw.get("pullRequestId", "")
    # The cleanest web URL is derived from project-relative path; fall
    # back to the API url when we can't reconstruct it.
    web_url = ""
    if project and repo_name and pr_id:
        # org lives in the path segment before the project in org_url.
        # Parse: https://dev.azure.com/<org>/_apis/git/repositories/<id>
        org = ""
        if org_url.startswith("https://"):
            tail = org_url[len("https://") :]
            segs = tail.split("/")
            if len(segs) >= 2:
                org = segs[1]
        if org:
            web_url = (
                f"https://{host}/{org}/{urllib.parse.quote(project)}/"
                f"_git/{urllib.parse.quote(repo_name)}/pullrequest/{pr_id}"
            )
    return PullRequest(
        number=int(pr_id or 0),
        title=str(raw.get("title") or ""),
        state=state,
        author=str(created_by.get("uniqueName") or created_by.get("displayName") or ""),
        head_ref=head_ref,
        base_ref=base_ref,
        head_sha=str((raw.get("lastMergeSourceCommit") or {}).get("commitId") or ""),
        url=web_url or org_url,
        body=str(raw.get("description") or ""),
        draft=bool(raw.get("isDraft") or False),
        raw=raw,
    )


def _to_repo_info(
    raw: dict[str, Any],
    host: str,
    org_name: str,
    project: str,
    private: bool,
) -> RepoInfo:
    name = str(raw.get("name") or "")
    remote_url = str(raw.get("remoteUrl") or raw.get("webUrl") or "")
    ssh_url = str(raw.get("sshUrl") or "")
    web_url = str(raw.get("webUrl") or "")
    if not web_url and name:
        web_url = (
            f"https://{host}/{urllib.parse.quote(org_name)}"
            f"/{urllib.parse.quote(project)}/_git/{urllib.parse.quote(name)}"
        )
    return RepoInfo(
        name=name,
        owner=f"{org_name}/{project}",
        clone_url=remote_url,
        ssh_url=ssh_url,
        html_url=web_url,
        private=private,
    )


def _to_comment(
    raw: dict[str, Any],
    slug: str,
    pr_number: int,
    thread_id: Any,
    host: str,
) -> Comment:
    author = raw.get("author") or {}
    comment_id = int(raw.get("id") or 0)
    # Azure doesn't provide a per-comment web URL; link to the PR +
    # discussion anchor is the closest we can do.
    org, project, repo = (slug.split("/", 2) + ["", "", ""])[:3]
    url = (
        f"https://{host}/{org}/{urllib.parse.quote(project)}/"
        f"_git/{urllib.parse.quote(repo)}/pullrequest/{pr_number}"
        f"?discussionId={thread_id}"
    )
    enriched = dict(raw)
    if thread_id is not None:
        enriched["_thread_id"] = thread_id
    return Comment(
        id=comment_id,
        author=str(author.get("uniqueName") or author.get("displayName") or ""),
        body=str(raw.get("content") or ""),
        created_at=str(raw.get("publishedDate") or raw.get("lastUpdatedDate") or ""),
        url=url,
        raw=enriched,
    )
