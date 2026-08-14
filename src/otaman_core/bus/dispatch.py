#!/usr/bin/env python3
"""bus dispatch — recipient resolution for outgoing bus messages.

When a message carries a ``path:`` field, recipients are derived from the
repo's ``owner-paths`` block via :func:`resolve_owners_for_paths`. When
``path:`` is absent the message routes via its ``to:`` field exactly as
before — no behaviour change for v1 polyrepo deployments.

This module is consumed by transport implementations (otaman-plugin's
bus_server, otaman-bridge's pm-sync handler, etc.). It contains pure
functions — no I/O, no transport coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otaman_core.owner_paths import (
    PlatformConfig,
    resolve_owners_for_paths,
)


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of recipient resolution for a single message.

    ``recipients`` is the unique set of agent names the bus should deliver
    to (one copy per recipient — the transport handles the fan-out).
    ``mode`` is informational: ``"to"`` when routing came from the bus
    frontmatter ``to:`` field, ``"path"`` when it was derived from
    ``owner-paths``, ``"multicast"`` when ``path:`` was a list and >1
    distinct agents matched.
    ``per_path`` is populated only for path-based modes; maps each input
    path to the resolved agent so callers can build per-recipient
    context (e.g. "you own apps/web/...").
    """

    recipients: list[str]
    mode: str
    per_path: dict[str, str]


class DispatchError(ValueError):
    """Raised when a message can't be dispatched.

    Currently covers: ``path:`` present but ``repo:`` missing; repo
    name not in platform.yaml. The bus surfaces this to the sender via
    a delivery-failure ack rather than silently dropping the message.
    """


def dispatch(
    message: dict[str, Any],
    platform: PlatformConfig,
) -> DispatchResult:
    """Resolve recipients for ``message`` against ``platform``.

    ``message`` is the parsed YAML frontmatter dict. Two routing modes:

      - ``path:`` absent → behaviour preserved; recipients come from
        ``to:``. ``to:`` may be ``"all"``, ``"human"``, a single agent
        name, or a comma-separated list. The dispatcher returns the
        parsed list as-is; broadcast whitelist enforcement is the
        validator's job (see :mod:`otaman_core.validate_message`).

      - ``path:`` present → recipients come from ``owner-paths``. Requires
        a ``repo:`` field naming the target repo. ``path:`` may be a
        string or a list; both yield a per-path resolution and the
        unique set of agents becomes the recipient list.

    The dispatcher never normalises or filters by message ``type:`` — the
    spec only constrains which types MAY use ``path:`` (the validator
    enforces that). Once a message is past the validator, every recipient
    in the result list is a legitimate delivery target.
    """
    path_field = message.get("path")

    if path_field is None:
        return _dispatch_by_to(message)

    repo_name = message.get("repo")
    if not isinstance(repo_name, str) or not repo_name:
        raise DispatchError(
            "message has 'path' but no 'repo'; path-based dispatch requires "
            "repo context (which platform.yaml repo the paths refer to)"
        )

    if isinstance(path_field, str):
        paths = [path_field]
    elif isinstance(path_field, list):
        paths = [str(p) for p in path_field if p]
        if not paths:
            raise DispatchError(
                "message 'path' is an empty list; either omit the field or "
                "supply at least one path string"
            )
    else:
        raise DispatchError(
            f"message 'path' must be a string or a list of strings, got {type(path_field).__name__}"
        )

    if platform.get_repo(repo_name) is None:
        raise DispatchError(f"repo {repo_name!r} not in platform.yaml; can't resolve owner-paths")

    per_path = resolve_owners_for_paths(platform, repo_name, paths)
    unique = sorted(set(per_path.values()))
    mode = "multicast" if len(unique) > 1 else "path"
    return DispatchResult(recipients=unique, mode=mode, per_path=per_path)


def _dispatch_by_to(message: dict[str, Any]) -> DispatchResult:
    """Route via the legacy ``to:`` field — recipients as declared."""
    to_field = message.get("to", "")
    if isinstance(to_field, list):
        recipients = [str(r).strip() for r in to_field if str(r).strip()]
    else:
        recipients = [r.strip() for r in str(to_field).split(",") if r.strip()]
    return DispatchResult(recipients=recipients, mode="to", per_path={})


__all__ = [
    "DispatchError",
    "DispatchResult",
    "dispatch",
]
