#!/usr/bin/env python3
"""PM tool sync — protocol definition layer for JTBD-37.

Defines the adapter contract for syncing OpenSpec changes and agent task
state to external project-management tools (Redmine, Linear, Jira, etc.).

Pattern mirrors ``HarnessAdapter`` from ADR-003: a ``Protocol`` class
declares the surface; concrete adapters live in ``pm_sync_<provider>.py``
and register themselves via ``register_pm_adapter``.

Typical platform.yaml shape::

    pm-sync:
      provider: redmine
      base_url: https://pm.example.com
      identity_mode: system_user
      program_name: "Otaman Platform"
      program_key: OTAN
      per_repo: true
      exclude_repos: []
      webhook_target: https://hooks.otaman.io/pm
      project_map:
        otaman-core: 12
        otaman-plugin: 17
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Capabilities


@dataclass(frozen=True)
class PmAdapterCapabilities:
    """Declares what a concrete PM adapter supports.

    Every field maps 1-to-1 to a PM-tool feature.  Routing and orchestration
    layers consult this at session-start to avoid calling methods the
    adapter cannot honour.
    """

    issue_comments: bool
    custom_fields: bool
    custom_workflow: bool
    webhook_inbound: bool
    webhook_registration_api: bool
    user_creation_api: bool
    agent_identity_user: bool
    agent_identity_group: bool
    agent_identity_system_user: bool
    mcp_support: bool
    rest_api: bool
    native_assignee_metrics: bool
    project_hierarchy: bool
    github_url_field: str | None
    project_custom_fields_api: bool


# ---------------------------------------------------------------------------
# Value types


@dataclass(frozen=True)
class PmSyncConfig:
    """Resolved ``pm-sync:`` block from platform.yaml."""

    provider: str
    base_url: str
    identity_mode: str
    program_name: str
    program_key: str
    per_repo: bool
    exclude_repos: list[str]
    webhook_target: str
    project_map: dict[str, int]
    status_map: dict[str, str]   # Otaman state → PM status name, e.g. {"declared": "New", "done": "Closed"}
    tracker: str                 # default issue tracker/type name, e.g. "Task"
    custom_fields: dict[str, int] | None = None
    """Optional override of Easy8/Redmine custom-field name → id mapping.

    When present, the adapter uses this map directly instead of auto-
    discovering field ids from ``/custom_fields.json``. Loaded from
    ``pm-sync.custom-fields`` in platform.yaml. None = let the adapter
    auto-discover at init time."""


@dataclass(frozen=True)
class PmProject:
    """Cross-provider view of a PM project (board / tracker / space)."""

    id: int
    name: str
    identifier: str
    parent_id: int | None


@dataclass(frozen=True)
class PmIssue:
    """Cross-provider view of a single issue / ticket."""

    id: int
    project_id: int
    subject: str
    status: str
    agent_name: str | None
    spec_path: str | None
    jtbd_id: str | None


@dataclass(frozen=True)
class PmIssueFilters:
    """Optional filter bag for ``PmSyncAdapter.list_issues``."""

    project_id: int | None = None
    status: str | None = None
    agent_name: str | None = None


@dataclass(frozen=True)
class PmStatus:
    """A workflow status value as returned by the PM tool."""

    id: int
    name: str


@dataclass(frozen=True)
class PmPriority:
    """A priority level as returned by the PM tool."""

    id: int
    name: str


@dataclass(frozen=True)
class PmInboundEvent:
    """Normalised view of a webhook payload from the PM tool.

    ``event_type`` is one of: ``issue_created``, ``issue_updated``,
    ``issue_deleted``.
    ``raw`` carries the full provider payload for callers that need it.
    """

    event_type: str
    project_id: int
    issue_id: int | None
    issue_subject: str | None
    new_status: str | None
    spec_path: str | None
    raw: dict


@dataclass(frozen=True)
class WebhookRegistration:
    """Handle returned after a successful webhook registration."""

    id: int
    url: str
    active: bool


@dataclass(frozen=True)
class SpecChange:
    """An OpenSpec change that needs to be reflected in the PM tool.

    ``description`` is the body of the PM issue (usually the change's
    ``proposal.md``). Empty when the bridge couldn't read the proposal
    or none exists; the adapter omits the description field in that
    case rather than sending an empty string.
    """

    change_name: str
    title: str
    agent_name: str
    spec_path: str
    jtbd_id: str | None
    description: str = ""


@dataclass(frozen=True)
class SpecState:
    """Current lifecycle state of a spec change.

    ``status`` is one of: ``declared``, ``in_progress``, ``blocked``, ``done``.
    """

    status: str


# ---------------------------------------------------------------------------
# Adapter Protocol


@runtime_checkable
class PmSyncAdapter(Protocol):
    """Provider-agnostic PM sync surface.

    Concrete adapters live in ``pm_sync_<provider>.py`` and register
    themselves with ``register_pm_adapter``.  All methods are synchronous;
    async wrappers are the adapter's own responsibility.

    Raises ``PmSyncError`` on API failure — callers decide whether to
    log-and-continue or bubble up.
    """

    @property
    def capabilities(self) -> PmAdapterCapabilities: ...

    def provision_project(self, config: PmSyncConfig) -> PmProject: ...

    def create_issue(self, spec_change: SpecChange) -> PmIssue: ...

    def update_issue(self, issue_id: int, state: SpecState) -> PmIssue: ...

    def add_comment(self, issue_id: int, body: str) -> None: ...

    def list_issues(self, filters: PmIssueFilters) -> list[PmIssue]: ...

    def register_webhook(self, url: str, events: list[str]) -> WebhookRegistration: ...

    def handle_inbound_event(self, payload: dict) -> PmInboundEvent: ...

    def list_statuses(self) -> list[PmStatus]: ...

    def list_priorities(self) -> list[PmPriority]: ...

    def get_users(self) -> list[dict]:
        """Return raw user records from the PM tool.

        Used by ``resolve_pm_user_id`` in otaman-adapters to look up
        :class:`~otaman_core.human_roster.HumanRosterEntry` rows by email
        (exact) then by name (case-insensitive). Field keys vary per
        provider; callers normalise.

        Example return shapes::

            # Easy8 / Redmine
            [{"id": 1, "name": "Roman", "mail": "r@x.com"}, ...]

            # Linear / Jira / etc — different keys; resolution logic
            # handles per-provider mapping.
        """
        ...


# ---------------------------------------------------------------------------
# Error type


class PmSyncError(RuntimeError):
    """Raised when a PM-tool API call fails in a way the caller must know about.

    Message is meant to be surfaced to the user / bus log as-is.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Adapter registry


_PM_ADAPTERS: dict[str, type[PmSyncAdapter]] = {}


def register_pm_adapter(name: str, cls: type) -> None:
    """Register a concrete PM adapter class under ``name``.

    Called from each ``pm_sync_<provider>.py`` module at import time::

        register_pm_adapter("redmine", RedmineAdapter)
    """
    _PM_ADAPTERS[name] = cls  # type: ignore[assignment]


def get_pm_adapter(name: str) -> type[PmSyncAdapter]:
    """Return the adapter class registered under ``name``.

    Raises ``KeyError`` if no adapter has been registered for that name,
    with a helpful message listing what is available.
    """
    try:
        return _PM_ADAPTERS[name]
    except KeyError:
        available = ", ".join(sorted(_PM_ADAPTERS)) or "<none>"
        raise KeyError(
            f"unknown PM adapter {name!r}. "
            f"Available: {available}. "
            f"Import the adapter module to register it."
        ) from None


def list_pm_adapters() -> list[str]:
    """Return the names of all registered PM adapters."""
    return sorted(_PM_ADAPTERS)


# ---------------------------------------------------------------------------
# platform.yaml helper


def load_pm_sync_config(platform_yaml_path: Path) -> PmSyncConfig | None:
    """Read the ``pm-sync:`` block from platform.yaml.

    Returns ``None`` when the file does not exist, cannot be parsed, or
    the block is absent.  Never raises; callers treat ``None`` as
    "PM sync not configured".
    """
    import yaml  # local import keeps the base module yaml-optional at import time

    if not platform_yaml_path.is_file():
        return None
    try:
        data = yaml.safe_load(platform_yaml_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None

    block = data.get("pm-sync")
    if not isinstance(block, dict):
        return None

    def _get(key_hyphen: str, key_under: str, default: object = None) -> object:
        """Read hyphenated YAML key, fall back to underscored form."""
        v = block.get(key_hyphen)
        if v is None:
            v = block.get(key_under)
        return v if v is not None else default

    custom_fields_raw = _get("custom-fields", "custom_fields")
    custom_fields: dict[str, int] | None
    if isinstance(custom_fields_raw, dict) and custom_fields_raw:
        try:
            custom_fields = {str(k): int(v) for k, v in custom_fields_raw.items()}
        except (TypeError, ValueError):
            # Bad shape (e.g., non-integer ids) — fall back to auto-discovery.
            custom_fields = None
    else:
        custom_fields = None

    try:
        return PmSyncConfig(
            provider=str(_get("provider", "provider") or ""),
            base_url=str(_get("base-url", "base_url") or ""),
            identity_mode=str(_get("identity-mode", "identity_mode") or ""),
            program_name=str(_get("program-name", "program_name") or ""),
            program_key=str(_get("program-key", "program_key") or ""),
            per_repo=bool(_get("per-repo", "per_repo", False)),
            exclude_repos=list(_get("exclude-repos", "exclude_repos") or []),
            webhook_target=str(_get("webhook-target", "webhook_target") or ""),
            project_map=dict(_get("project-map", "project_map") or {}),
            status_map=dict(_get("status-map", "status_map") or {}),
            tracker=str(_get("tracker", "tracker") or "Task"),
            custom_fields=custom_fields,
        )
    except (TypeError, ValueError):
        return None
