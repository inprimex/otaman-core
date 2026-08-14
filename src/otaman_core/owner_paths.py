#!/usr/bin/env python3
"""owner-paths — per-path agent ownership inside a single repo (JTBD-48).

Monorepos partition ownership across agents via the ``owner-paths:`` block
on ``platform.yaml repos[]`` entries::

    repos:
      - name: my-monorepo
        owner: root-agent          # catch-all + root config
        owner-paths:
          "apps/web/**":     web-agent
          "apps/api/**":     api-agent
          "packages/shared/**": shared-agent

This module defines the typed representation (:class:`RepoConfig`,
:class:`PlatformConfig`) and the resolution algorithm
(:func:`resolve_owner_for_path`, :func:`resolve_owners_for_paths`) consumed
by the bus dispatcher and CLI helpers.

Glob semantics follow gitignore conventions: ``**`` matches zero or more
path segments, ``*`` matches within a single segment, and the
specificity tie-break is by pattern length (longer pattern wins).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Dataclasses


@dataclass(frozen=True)
class RepoConfig:
    """A single ``repos[]`` entry from platform.yaml.

    ``owner_paths`` is empty when the repo isn't partitioned; the whole
    repo then belongs to ``owner``. When non-empty, paths matched by a
    glob route to that glob's agent; unmatched paths fall back to
    ``owner`` (the catch-all).
    """

    name: str
    owner: str
    owner_paths: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PlatformConfig:
    """Minimal typed view of platform.yaml — only the bits owner-paths needs.

    Other top-level fields (specs, communication, git_host, etc.) stay in
    the JSON-Schema-validated raw dict; this dataclass is intentionally
    narrow so resolution code doesn't grow tendrils into unrelated
    sections of platform.yaml.
    """

    repos: list[RepoConfig] = field(default_factory=list)

    def get_repo(self, name: str) -> RepoConfig | None:
        """Return the :class:`RepoConfig` for ``name`` or None if absent."""
        for r in self.repos:
            if r.name == name:
                return r
        return None


class OwnerPathsError(ValueError):
    """Raised when ``owner_paths`` is structurally invalid.

    Distinct from JSON-Schema validation (which is enforced by
    ``validate_platform``) because this captures semantic constraints
    that schemas can't express cleanly — e.g., "every agent referenced
    in owner_paths must be declared elsewhere in platform.yaml".
    """


# ---------------------------------------------------------------------------
# Loader


def parse_platform_config(data: dict[str, Any]) -> PlatformConfig:
    """Build a :class:`PlatformConfig` from a parsed platform.yaml dict.

    Tolerant of missing/empty fields; structural type errors raise
    :class:`OwnerPathsError`. JSON-Schema validation should run separately.
    """
    repos_raw = data.get("repos") or []
    if not isinstance(repos_raw, list):
        raise OwnerPathsError(f"repos: expected a list, got {type(repos_raw).__name__}")

    repos: list[RepoConfig] = []
    for i, r in enumerate(repos_raw):
        if not isinstance(r, dict):
            raise OwnerPathsError(f"repos[{i}]: expected a mapping, got {type(r).__name__}")
        name = r.get("name")
        owner = r.get("owner")
        # Accept hyphenated and underscored forms; the schema canonical is hyphenated.
        owner_paths_raw = r.get("owner-paths")
        if owner_paths_raw is None:
            owner_paths_raw = r.get("owner_paths")

        if not isinstance(name, str) or not name:
            raise OwnerPathsError(f"repos[{i}]: 'name' is required and must be a non-empty string")
        if not isinstance(owner, str) or not owner:
            raise OwnerPathsError(
                f"repos[{i}] {name!r}: 'owner' is required and must be a non-empty string"
            )

        owner_paths: dict[str, str] = {}
        if owner_paths_raw is None:
            pass  # no per-path overrides
        elif not isinstance(owner_paths_raw, dict):
            raise OwnerPathsError(
                f"repos[{i}] {name!r}: 'owner-paths' must be a mapping, got "
                f"{type(owner_paths_raw).__name__}"
            )
        else:
            for pattern, agent in owner_paths_raw.items():
                if not isinstance(pattern, str) or not pattern:
                    raise OwnerPathsError(
                        f"repos[{i}] {name!r}: owner-paths keys must be non-empty strings"
                    )
                if not isinstance(agent, str) or not agent:
                    raise OwnerPathsError(
                        f"repos[{i}] {name!r}: owner-paths[{pattern!r}] must map "
                        "to a non-empty agent name"
                    )
                owner_paths[pattern] = agent

        repos.append(RepoConfig(name=name, owner=owner, owner_paths=owner_paths))

    return PlatformConfig(repos=repos)


def load_platform_config(platform_yaml_path: Path) -> PlatformConfig | None:
    """Read platform.yaml and return a :class:`PlatformConfig`.

    Returns ``None`` when the file is missing or unparseable. Structural
    errors in present data raise :class:`OwnerPathsError` so they surface
    at validation / startup time rather than silently routing messages to
    the wrong agent.
    """
    import yaml  # local import — keep module yaml-optional at import time

    if not platform_yaml_path.is_file():
        return None
    try:
        data = yaml.safe_load(platform_yaml_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return parse_platform_config(data)


# ---------------------------------------------------------------------------
# Glob matching (gitignore semantics, simplified for Phase 1)


def _match_path(path: str, pattern: str) -> bool:
    """Return True iff ``path`` matches the gitignore-style ``pattern``.

    Supported syntax (Phase 1):
      - ``**`` matches zero or more path segments (greedy across ``/``)
      - ``*``  matches within a single segment (no ``/``)
      - Leading ``/`` anchors to the (notional) repo root
      - A pattern with no ``/`` matches the basename at any depth
      - Negation (``!``) is NOT supported — spec explicitly excludes it

    Implementation: translate the pattern to a regex and match. Python's
    ``fnmatch.translate`` doesn't handle ``**`` correctly across slashes,
    so we do a small translation pass ourselves.
    """
    import re

    norm = path.strip("/").replace("\\", "/")

    anchored = pattern.startswith("/")
    pat = pattern.lstrip("/")

    # A pattern with no slash matches the basename at any depth.
    if "/" not in pat:
        # Phase 1: anchored "/foo" still has no inner slash → match root only;
        # plain "foo" → match basename anywhere.
        if anchored:
            return _match_segment(norm.split("/")[0], pat) and "/" not in norm
        for segment in norm.split("/"):
            if _match_segment(segment, pat):
                return True
        return False

    # Translate ** and * to regex
    regex_parts = []
    i = 0
    while i < len(pat):
        if pat[i : i + 3] == "**/":
            regex_parts.append("(?:.*/)?")
            i += 3
        elif pat[i : i + 2] == "**":
            regex_parts.append(".*")
            i += 2
        elif pat[i] == "*":
            regex_parts.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            regex_parts.append("[^/]")
            i += 1
        else:
            regex_parts.append(re.escape(pat[i]))
            i += 1
    regex = "^" + "".join(regex_parts) + "$"
    return re.match(regex, norm) is not None


def _match_segment(segment: str, pattern: str) -> bool:
    """fnmatch within a single path segment (no slashes)."""
    return fnmatch.fnmatchcase(segment, pattern)


# ---------------------------------------------------------------------------
# Resolution


def resolve_owner_for_path(
    platform: PlatformConfig,
    repo_name: str,
    path: str,
) -> str:
    """Return the agent that owns ``path`` within ``repo_name``.

    Algorithm:
      1. Look up the :class:`RepoConfig` for ``repo_name``; raise
         :class:`ValueError` if absent (caller must know the repo exists).
      2. For each glob in ``owner_paths``, check if ``path`` matches.
      3. Among matching globs, the most specific (longest pattern string)
         wins. Ties (equal length, both match) resolve to whichever was
         declared first — Python dict iteration order preserves this.
      4. If no glob matches, fall back to the repo's catch-all ``owner``.
    """
    repo = platform.get_repo(repo_name)
    if repo is None:
        raise ValueError(f"repo {repo_name!r} not in platform.yaml")

    best: tuple[int, str] | None = None
    for pattern, agent in repo.owner_paths.items():
        if _match_path(path, pattern):
            specificity = len(pattern)
            if best is None or specificity > best[0]:
                best = (specificity, agent)
    return best[1] if best else repo.owner


def resolve_owners_for_paths(
    platform: PlatformConfig,
    repo_name: str,
    paths: list[str],
) -> dict[str, str]:
    """Multi-path variant: ``{path: owner_agent}`` for each path.

    Callers reduce the unique value set to build cc: recipients for
    multicast dispatch. Empty ``paths`` returns an empty dict.
    """
    return {p: resolve_owner_for_path(platform, repo_name, p) for p in paths}


# ---------------------------------------------------------------------------
# Validation


@dataclass(frozen=True)
class OwnerPathsIssue:
    """One validation finding from :func:`validate_owner_paths`."""

    severity: str  # "error" | "warning"
    repo: str
    message: str


def validate_owner_paths(
    platform: PlatformConfig,
    known_agents: set[str],
) -> list[OwnerPathsIssue]:
    """Check every repo's owner_paths block.

    Reports:
      - ERROR for any agent referenced in owner_paths that isn't in
        ``known_agents``.
      - WARNING for overlapping globs of equal specificity (same length).
        Phase 1 tie-break is "first declared wins", but operators should
        know the overlap exists.

    Phase 1 does NOT validate glob syntax (Python's fnmatch + our
    translator are tolerant). Operators using exotic patterns get
    surprising matches; that's a Phase 2 improvement if it bites.
    """
    issues: list[OwnerPathsIssue] = []
    for repo in platform.repos:
        if not repo.owner_paths:
            continue

        # Unknown-agent check
        for pattern, agent in repo.owner_paths.items():
            if agent not in known_agents:
                issues.append(
                    OwnerPathsIssue(
                        severity="error",
                        repo=repo.name,
                        message=(
                            f"owner-paths[{pattern!r}] references unknown agent "
                            f"{agent!r}; declare it in platform.yaml agents: first"
                        ),
                    )
                )

        # Overlap check — equal-length patterns where one matches the other's
        # example path are ambiguous. We use the pattern itself as a probe.
        patterns = list(repo.owner_paths.keys())
        for i, p1 in enumerate(patterns):
            for p2 in patterns[i + 1 :]:
                if len(p1) != len(p2):
                    continue
                # Use a representative path: strip glob chars from p1.
                probe = p1.replace("**", "x").replace("*", "x").replace("?", "x")
                if _match_path(probe, p1) and _match_path(probe, p2):
                    issues.append(
                        OwnerPathsIssue(
                            severity="warning",
                            repo=repo.name,
                            message=(
                                f"owner-paths patterns {p1!r} and {p2!r} have equal "
                                f"specificity and both match {probe!r}; first wins"
                            ),
                        )
                    )
    return issues


__all__ = [
    "OwnerPathsError",
    "OwnerPathsIssue",
    "PlatformConfig",
    "RepoConfig",
    "load_platform_config",
    "parse_platform_config",
    "resolve_owner_for_path",
    "resolve_owners_for_paths",
    "validate_owner_paths",
]
