#!/usr/bin/env python3
"""Git host integration — provider detection + PAT storage + validation.

Phase 1: enough primitives that the rest of otaman knows *which* git
host a project is on and has a PAT it can use. No API beyond a whoami/
validate call here — the read (PR metadata) and write (post observer
comments) paths land in later phases.

Providers recognized:
  - github        — github.com and GitHub Enterprise (any host, slug x/y)
  - gitlab        — gitlab.com and self-hosted GitLab
  - bitbucket     — bitbucket.org (Cloud); Bitbucket Server/DC matches
                    the URL shape but has a different REST base — we
                    flag the cloud vs server distinction when known
  - azure-devops  — dev.azure.com / visualstudio.com
  - gitea         — self-hosted Gitea instances (always self-hosted)
  - forgejo       — self-hosted Forgejo instances (Gitea-compatible API)
  - unknown       — couldn't classify; no integration possible

Token storage uses the existing ``_secrets`` chain (env → dotenv →
keyring). Tokens never enter platform.yaml. The config-side entry
points to a ``SecretRef``; callers resolve at runtime.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otaman_core._secrets import SecretRef, resolve

# ---------------------------------------------------------------------------
# Provider classification


Provider = str  # "github" | "gitlab" | "bitbucket" | "azure-devops" | "unknown"


@dataclass
class RemoteInfo:
    """Parsed view of a ``git remote`` URL."""

    provider: Provider
    host: str  # "github.com" | "gitlab.mycorp.io" | …
    owner: str  # user or org (or Azure "organization/project")
    repo: str  # bare repo name without .git suffix
    raw_url: str  # original remote URL

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def is_self_hosted(self) -> bool:
        """True when host isn't one of the standard SaaS hosts."""
        return self.host not in (
            "github.com",
            "gitlab.com",
            "bitbucket.org",
            "dev.azure.com",
            "ssh.dev.azure.com",
        )


# Regex grammar:
#   - SSH shape:    git@host:owner/repo(.git)?
#   - SSH shape v2: ssh://git@host/owner/repo(.git)?
#   - HTTPS shape:  https://[user@]host/owner/repo(.git)?
#   - Azure DevOps: https://dev.azure.com/org/project/_git/repo
_RE_SSH_LEGACY = re.compile(r"^(?P<user>[^@\s]+)@(?P<host>[^:]+):(?P<path>.+?)(\.git)?$")
_RE_SSH_URL = re.compile(r"^ssh://(?:[^@]+@)?(?P<host>[^/]+)/(?P<path>.+?)(\.git)?$")
_RE_HTTPS = re.compile(r"^https?://(?:[^@/]+@)?(?P<host>[^/]+)/(?P<path>.+?)(\.git)?$")
_RE_AZDO_PATH = re.compile(r"^(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>.+)$")


def parse_remote_url(url: str, *, provider_hint: Provider | None = None) -> RemoteInfo | None:
    """Classify a remote URL. Returns None if the URL doesn't parse.

    Accepts the three common shapes (legacy SSH, SSH URL, HTTPS) plus
    Azure DevOps's quirky ``/_git/`` path. Silent on garbage input so
    callers can use this for best-effort classification.

    ``provider_hint`` lets callers tell us which provider a self-hosted
    host belongs to (typically from ``platform.yaml`` ``git_host.provider:``).
    Used for self-hosted Gitea/Forgejo instances whose hostnames aren't
    classifiable by pattern alone. The hint only applies when the URL
    host doesn't match a known SaaS pattern; it never overrides github.com,
    gitlab.com, etc.
    """
    url = (url or "").strip()
    if not url:
        return None

    m = _RE_SSH_LEGACY.match(url)
    if m:
        return _classify(m.group("host"), m.group("path"), url, provider_hint)
    m = _RE_SSH_URL.match(url)
    if m:
        return _classify(m.group("host"), m.group("path"), url, provider_hint)
    m = _RE_HTTPS.match(url)
    if m:
        return _classify(m.group("host"), m.group("path"), url, provider_hint)
    return None


def _classify(
    host: str,
    path: str,
    raw_url: str,
    provider_hint: Provider | None = None,
) -> RemoteInfo:
    host = host.strip().lower()
    path = path.strip().strip("/").removesuffix(".git")

    # Azure DevOps shape first (more specific).
    if host in ("dev.azure.com", "ssh.dev.azure.com"):
        m = _RE_AZDO_PATH.match(path)
        if m:
            return RemoteInfo(
                provider="azure-devops",
                host=host,
                owner=f"{m.group('org')}/{m.group('project')}",
                repo=m.group("repo"),
                raw_url=raw_url,
            )

    # Provider from known hosts first.
    if host == "github.com" or host.startswith("github."):
        provider = "github"
    elif host == "gitlab.com" or host.startswith("gitlab."):
        provider = "gitlab"
    elif host == "bitbucket.org":
        provider = "bitbucket"
    else:
        # Self-hosted instance. Host alone doesn't identify the flavour
        # (Gitea/Forgejo can live anywhere). Fall back to the caller's
        # provider hint if supplied; otherwise "unknown".
        if provider_hint in ("gitea", "forgejo", "github", "gitlab", "bitbucket", "azure-devops"):
            provider = provider_hint
        else:
            provider = "unknown"

    parts = path.split("/", 1)
    if len(parts) != 2:
        return RemoteInfo(provider, host, path, "", raw_url)
    owner, repo = parts
    return RemoteInfo(provider, host, owner, repo, raw_url)


def detect_remote_for_repo(repo_dir: Path) -> RemoteInfo | None:
    """Read ``git remote get-url origin`` in ``repo_dir`` and parse it.

    Returns None if ``repo_dir`` isn't a git working tree, has no
    origin remote, or the URL doesn't parse into a known shape.
    """
    if not (repo_dir / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_remote_url(result.stdout.strip())


def detect_remotes_for_maestro(
    maestro_root: Path,
) -> list[tuple[str, RemoteInfo | None]]:  # legacy: renamed detect_remotes_for_otaman at 1.0
    """Walk repos listed in platform.yaml; return ``(repo_name, RemoteInfo|None)``.

    Used by ``otaman doctor`` to summarize "what git hosts is this
    project actually hooked up to?" in one glance.
    """
    import yaml

    platform_yaml = maestro_root / "platform.yaml"
    if not platform_yaml.is_file():
        return []
    try:
        data = yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []

    out: list[tuple[str, RemoteInfo | None]] = []
    for repo_cfg in data.get("repos") or []:
        if not isinstance(repo_cfg, dict):
            continue
        name = repo_cfg.get("name")
        path = repo_cfg.get("path")
        if not name or not path:
            continue
        repo_dir = (maestro_root / path).resolve()
        out.append((name, detect_remote_for_repo(repo_dir)))
    return out


# ---------------------------------------------------------------------------
# platform.yaml git_host: block


@dataclass
class GitHostConfig:
    """Resolved `git_host:` entry from platform.yaml.

    YAML shape::

        git_host:
          provider: github              # required
          host: github.com              # optional; defaults to SaaS host per provider
          token:                        # required; see scripts/_secrets.py
            sources:
              - { type: env,    name: MAESTRO_GH_TOKEN }
              - { type: dotenv, name: MAESTRO_GH_TOKEN }
          default_scope: [read, write]  # optional, documents intent (not enforced)
    """

    provider: Provider
    host: str
    token_ref: SecretRef
    default_scope: list[str]
    org: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GitHostConfig:
        provider = str(data.get("provider") or "").strip().lower()
        if not provider:
            raise ValueError("git_host: provider is required")
        host = str(data.get("host") or default_host_for(provider)).strip().lower()
        token = data.get("token")
        if token is None:
            raise ValueError("git_host: token is required (see scripts/_secrets.py)")
        token_ref = SecretRef.from_config(token)
        scope_raw = data.get("default_scope") or []
        if not isinstance(scope_raw, list):
            raise ValueError("git_host: default_scope must be a list")
        org_raw = data.get("org")
        org = str(org_raw).strip() if org_raw else None
        return cls(
            provider=provider,
            host=host,
            token_ref=token_ref,
            default_scope=[str(s).strip().lower() for s in scope_raw if s],
            org=org or None,
        )


def default_host_for(provider: Provider) -> str:
    return {
        "github": "github.com",
        "gitlab": "gitlab.com",
        "bitbucket": "bitbucket.org",
        "azure-devops": "dev.azure.com",
        "gitea": "",  # always self-hosted; host: required in platform.yaml
        "forgejo": "",  # same
    }.get(provider, "")


def load_git_host_config(maestro_root: Path) -> GitHostConfig | None:
    """Read ``git_host:`` from platform.yaml. None when absent."""
    import yaml

    platform_yaml = maestro_root / "platform.yaml"
    if not platform_yaml.is_file():
        return None
    try:
        data = yaml.safe_load(platform_yaml.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    block = data.get("git_host")
    if not isinstance(block, dict):
        return None
    try:
        return GitHostConfig.from_dict(block)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Token validation — one lightweight API call per provider


@dataclass
class ValidationResult:
    ok: bool
    identity: str = ""  # "octocat", "tanuki@gitlab.com", …
    scopes: list[str] | None = None
    error: str = ""


def validate_token(provider: Provider, host: str, token: str) -> ValidationResult:
    """Call the provider's whoami/me endpoint with the PAT.

    Intentionally minimal — just proves "this token talks to this API"
    so `otaman doctor` can flag expired / revoked tokens. Each
    provider has its own endpoint shape, so we branch here rather
    than force a common adapter that would only paper over the
    differences.
    """
    if provider == "github":
        return _validate_github(host, token)
    if provider == "gitlab":
        return _validate_gitlab(host, token)
    if provider == "bitbucket":
        return _validate_bitbucket(token)
    if provider == "azure-devops":
        return _validate_azure(host, token)
    return ValidationResult(ok=False, error=f"unknown provider: {provider!r}")


def _do_get(
    url: str, headers: dict[str, str], timeout: float = 5.0
) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", dict(e.headers or {})
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"request failed: {e}") from e


def _validate_github(host: str, token: str) -> ValidationResult:
    # GitHub SaaS → api.github.com; Enterprise → {host}/api/v3
    base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    try:
        status, body, headers = _do_get(
            f"{base}/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "otaman-plugin",
            },
        )
    except RuntimeError as e:
        return ValidationResult(ok=False, error=str(e))
    if status == 200:
        try:
            data = json.loads(body.decode("utf-8"))
            login = data.get("login", "")
        except (ValueError, UnicodeDecodeError):
            login = ""
        scopes_header = headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or ""
        scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
        return ValidationResult(ok=True, identity=login, scopes=scopes)
    return ValidationResult(ok=False, error=f"HTTP {status}")


def _validate_gitlab(host: str, token: str) -> ValidationResult:
    base = f"https://{host}"
    try:
        status, body, _headers = _do_get(
            f"{base}/api/v4/user",
            headers={"PRIVATE-TOKEN": token, "User-Agent": "otaman-plugin"},
        )
    except RuntimeError as e:
        return ValidationResult(ok=False, error=str(e))
    if status == 200:
        try:
            data = json.loads(body.decode("utf-8"))
            return ValidationResult(ok=True, identity=data.get("username", ""))
        except (ValueError, UnicodeDecodeError):
            return ValidationResult(ok=True)
    return ValidationResult(ok=False, error=f"HTTP {status}")


def _validate_bitbucket(token: str) -> ValidationResult:
    try:
        status, body, _headers = _do_get(
            "https://api.bitbucket.org/2.0/user",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "otaman-plugin",
            },
        )
    except RuntimeError as e:
        return ValidationResult(ok=False, error=str(e))
    if status == 200:
        try:
            data = json.loads(body.decode("utf-8"))
            return ValidationResult(ok=True, identity=data.get("username", ""))
        except (ValueError, UnicodeDecodeError):
            return ValidationResult(ok=True)
    return ValidationResult(ok=False, error=f"HTTP {status}")


def _validate_azure(host: str, token: str) -> ValidationResult:
    # Azure DevOps accepts Basic auth with empty user + PAT as password.
    import base64

    auth = base64.b64encode(f":{token}".encode()).decode("ascii")
    try:
        status, body, _headers = _do_get(
            f"https://{host}/_apis/connectionData?api-version=7.1",
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent": "otaman-plugin",
            },
        )
    except RuntimeError as e:
        return ValidationResult(ok=False, error=str(e))
    if status == 200:
        try:
            data = json.loads(body.decode("utf-8"))
            ident = data.get("authenticatedUser") or {}
            return ValidationResult(ok=True, identity=ident.get("providerDisplayName", ""))
        except (ValueError, UnicodeDecodeError):
            return ValidationResult(ok=True)
    return ValidationResult(ok=False, error=f"HTTP {status}")


def resolve_and_validate(
    cfg: GitHostConfig,
    *,
    maestro_root: Path | None = None,
) -> ValidationResult:
    """Resolve the token from its SecretRef and call ``validate_token``.

    Returns a ValidationResult with ``ok=False, error=<why>`` if the
    token couldn't be found in any of the configured sources.
    """
    token = resolve(cfg.token_ref, maestro_root=maestro_root)
    if not token:
        return ValidationResult(
            ok=False,
            error="token not found in configured sources (env / .otaman/secrets.env / keychain)",
        )
    return validate_token(cfg.provider, cfg.host, token)


# ---------------------------------------------------------------------------
# Phase 2 — provider-neutral adapter for PR reads + comment writes


@dataclass
class PullRequest:
    """Minimal cross-provider view of a PR / MR.

    Only fields that multiple providers can actually populate. Raw
    provider payload rides along in ``raw`` for callers who need
    GitHub-specific / GitLab-specific detail.
    """

    number: int
    title: str
    state: str  # "open" | "closed" | "merged"
    author: str
    head_ref: str  # source branch
    base_ref: str  # target branch
    head_sha: str
    url: str  # web URL (for humans)
    body: str = ""  # PR description
    draft: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Comment:
    """A PR / MR comment (general thread, not a line-level review)."""

    id: int
    author: str
    body: str
    created_at: str  # ISO-8601 from the provider
    url: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepoInfo:
    """Cross-provider view of a remote git repository.

    Returned by ``GitHostAdapter.create_repo`` and used by callers that
    need to clone the freshly-created repo or persist its URLs in
    platform.yaml.
    """

    name: str
    owner: str  # org or user that owns the repo
    clone_url: str  # HTTPS clone URL (https://host/owner/repo.git)
    ssh_url: str  # SSH clone URL (git@host:owner/repo.git)
    html_url: str  # web URL for humans
    private: bool


class GitHostAdapter(Protocol):
    """Provider-agnostic PR read + comment write surface.

    Adapters live in ``git_host_<provider>.py`` (one per provider).
    All methods take the repo slug in canonical ``owner/repo`` form
    (Azure DevOps uses ``org/project/repo`` as a single string and
    the adapter normalises internally).

    Methods raise ``GitHostError`` on API failure — the caller decides
    whether to log-and-continue or bubble up.
    """

    provider: Provider
    host: str

    def list_open_prs(self, slug: str) -> list[PullRequest]: ...
    def get_pr(self, slug: str, number: int) -> PullRequest: ...
    def get_pr_for_branch(
        self,
        slug: str,
        branch: str,
    ) -> PullRequest | None: ...
    def post_comment(
        self,
        slug: str,
        pr_number: int,
        body: str,
    ) -> Comment: ...
    def list_comments(
        self,
        slug: str,
        pr_number: int,
    ) -> list[Comment]: ...

    # Repo lifecycle — used by `otaman project add` / `otaman project remove --delete-remote`
    def create_repo(
        self,
        name: str,
        org: str | None,
        private: bool = True,
        description: str = "",
    ) -> RepoInfo: ...

    def delete_repo(self, owner: str, name: str) -> None: ...


class GitHostError(RuntimeError):
    """Raised when a git-host API call fails in a way the caller needs
    to know about (auth, 404, 5xx, network timeout). Message is meant
    to be surfaced to the user as-is."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def get_adapter(
    cfg: GitHostConfig,
    *,
    maestro_root: Path | None = None,
) -> GitHostAdapter:
    """Build a concrete adapter for ``cfg``.

    Resolves the SecretRef once so the adapter holds a ready-to-use
    token. Raises ``GitHostError`` if the token can't be resolved —
    no point constructing an adapter that will fail every call.
    """
    token = resolve(cfg.token_ref, maestro_root=maestro_root)
    if not token:
        raise GitHostError(
            "git-host token could not be resolved from configured "
            "sources (env / .otaman/secrets.env / keychain).",
        )

    # Lazy imports so the base module stays provider-agnostic and a
    # partial install that's missing one adapter doesn't break the rest.
    if cfg.provider == "github":
        from otaman_core.git_host_github import GitHubAdapter  # noqa: PLC0415

        return GitHubAdapter(host=cfg.host, token=token)
    if cfg.provider == "gitlab":
        from otaman_core.git_host_gitlab import GitLabAdapter  # noqa: PLC0415

        return GitLabAdapter(host=cfg.host, token=token)
    if cfg.provider == "bitbucket":
        from otaman_core.git_host_bitbucket import BitbucketAdapter  # noqa: PLC0415

        return BitbucketAdapter(host=cfg.host, token=token)
    if cfg.provider == "azure-devops":
        from otaman_core.git_host_azure import AzureDevOpsAdapter  # noqa: PLC0415

        return AzureDevOpsAdapter(host=cfg.host, token=token)
    if cfg.provider in ("gitea", "forgejo"):
        from otaman_core.git_host_gitea import GiteaAdapter  # noqa: PLC0415

        return GiteaAdapter(host=cfg.host, token=token, provider=cfg.provider)

    raise GitHostError(
        f"unknown provider {cfg.provider!r}. "
        f"Supported: github, gitlab, bitbucket, azure-devops, gitea, forgejo."
    )
