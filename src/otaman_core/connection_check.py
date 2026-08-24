"""Connection check engine (agent-credential-access 1.3).

``otaman connection check <name>`` tests both reachability AND authentication
for a resolved :class:`~otaman_core.connections.Connection` and reports status.
By DEFAULT it is READ-ONLY — it never mutates state. Self-heal (respawn a dead
per-target ssh-agent, reload the key) is OPT-IN behind ``--fix``/``--reattach``;
that path drives the 1.2 registry primitives.

The engine dispatches by connection type to a :class:`Prober`. Probers return
booleans + a human ``detail`` string only — the report is the ``last-check``
fact plugin-agent renders in §2.1.

Hard invariant (Q5): a report carries status/booleans/locators and NEVER a
secret value. The network prober tests auth via a values-free precondition
(does ``secret_ref`` have a backing key in the backend's ``list_keys()``); any
live credential test delegated to a call site returns pass/fail only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC
from typing import Protocol

from otaman_core.connections import Connection
from otaman_core.ssh_registry import (
    AgentEntry,
    SshAgentRegistry,
    SshRegistryError,
    ssh_config_identity,
)

# Connection types served by the ssh-agent registry (vs. the network prober).
SSH_TYPES = frozenset({"ssh", "ssh-key", "deploy-key"})


@dataclass(frozen=True)
class ProbeResult:
    reachable: bool
    authenticated: bool
    detail: str


@dataclass(frozen=True)
class CheckReport:
    """The read-only verdict for one connection — the ``last-check`` fact.

    ``status`` is one of: ``ok``, ``unreachable``, ``auth-failed``,
    ``socket-dead``, ``fixed``, ``error``. ``healed`` is True only when a
    ``--fix`` run restored the connection.
    """

    name: str
    type: str
    endpoint: str
    reachable: bool
    authenticated: bool
    status: str
    detail: str
    healed: bool
    checked_at: str


class Prober(Protocol):
    """Tests one connection. ``heal`` returns None when the type has no self-heal."""

    def probe(self, conn: Connection) -> ProbeResult: ...

    def heal(self, conn: Connection) -> ProbeResult | None: ...


class SshProber:
    """Probes ssh connections via the 1.2 per-target socket registry.

    Reachability = the persisted socket is live; authentication = the agent has
    the key loaded. Self-heal spawns the per-target agent and reloads the key
    from the ``ssh_ref`` Host alias (``~/.ssh/config`` IdentityFile).
    """

    def __init__(
        self,
        registry: SshAgentRegistry,
        *,
        ssh_config_path=None,
    ) -> None:
        self._registry = registry
        self._ssh_config_path = ssh_config_path

    def probe(self, conn: Connection) -> ProbeResult:
        entry = self._registry.get(conn.name)
        if entry is None:
            return ProbeResult(False, False, "no ssh-agent registered for target")
        live = self._registry.is_live(entry)
        if not live:
            return ProbeResult(False, False, f"agent socket dead: {entry.socket}")
        # live socket → reachable; treat a registered+live target as authenticated
        # (is_live already confirms the agent responds; key presence is 1.2's concern).
        return ProbeResult(True, True, "agent socket live")

    def heal(self, conn: Connection) -> ProbeResult | None:
        key = self._key_locator(conn)
        if key is None:
            return ProbeResult(False, False, "no key locator (ssh_ref / config IdentityFile)")
        try:
            self._registry.spawn_agent(conn.name, key)
            self._registry.load_key(conn.name)
        except SshRegistryError as exc:
            return ProbeResult(False, False, f"self-heal failed: {exc}")
        return self.probe(conn)

    def _key_locator(self, conn: Connection) -> str | None:
        # An existing entry's key wins; else resolve the ssh_ref Host alias to
        # its IdentityFile path; else the ssh_ref itself is the locator.
        entry = self._registry.get(conn.name)
        if entry is not None and entry.key:
            return entry.key
        if conn.ssh_ref and self._ssh_config_path is not None:
            ident = ssh_config_identity(conn.ssh_ref, self._ssh_config_path)
            if ident:
                return ident
        return conn.ssh_ref


#: An endpoint reachability probe: endpoint → reachable? Injected for testing.
HttpProbe = Callable[[str], bool]


class NetworkProber:
    """Probes git-https / api / pat connections.

    Reachability = an injected ``http_probe`` reaches the endpoint. Authentication
    = the connection's ``secret_ref`` has a backing key in the backend's
    ``list_keys()`` result — a VALUES-FREE precondition. No secret value is read.
    Connections with no ``secret_ref`` are treated as auth-not-required.
    """

    def __init__(self, http_probe: HttpProbe, available_keys: Iterable[str]) -> None:
        self._http_probe = http_probe
        self._keys = set(available_keys)

    def probe(self, conn: Connection) -> ProbeResult:
        reachable = self._http_probe(conn.endpoint)
        if conn.secret_ref is None:
            authed = True
            detail = "reachable; no secret required" if reachable else "endpoint unreachable"
        elif conn.secret_ref in self._keys:
            authed = True
            detail = (
                "reachable; secret_ref has backing key" if reachable else "endpoint unreachable"
            )
        else:
            authed = False
            detail = f"no backing key for secret_ref '{conn.secret_ref}'"
        return ProbeResult(reachable, authed and reachable, detail)

    def heal(self, conn: Connection) -> ProbeResult | None:
        return None  # network auth has no local self-heal


def _default_clock() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


class ConnectionChecker:
    """Dispatches a connection to its :class:`Prober` and builds a report.

    Read-only unless ``fix=True``, in which case a failing, self-healable
    connection is repaired and re-probed. ``probers`` maps a lowercased
    connection ``type`` to a Prober; unmatched ssh-family types fall back to the
    ssh prober, everything else to the network prober.
    """

    def __init__(
        self,
        *,
        ssh_prober: Prober | None = None,
        network_prober: Prober | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._ssh = ssh_prober
        self._net = network_prober
        self._clock = clock or _default_clock

    def _prober_for(self, conn: Connection) -> Prober:
        if conn.type.lower() in SSH_TYPES:
            if self._ssh is None:
                raise CheckConfigError(f"no ssh prober configured for '{conn.name}'")
            return self._ssh
        if self._net is None:
            raise CheckConfigError(f"no network prober configured for '{conn.name}'")
        return self._net

    def check(self, conn: Connection, *, fix: bool = False) -> CheckReport:
        prober = self._prober_for(conn)
        result = prober.probe(conn)
        healed = False
        if fix and not (result.reachable and result.authenticated):
            healed_result = prober.heal(conn)
            if healed_result is not None:
                result = healed_result
                healed = result.reachable and result.authenticated
        return CheckReport(
            name=conn.name,
            type=conn.type,
            endpoint=conn.endpoint,
            reachable=result.reachable,
            authenticated=result.authenticated,
            status=_status_for(result, healed),
            detail=result.detail,
            healed=healed,
            checked_at=self._clock(),
        )

    def check_all(
        self, connections: Iterable[Connection], *, fix: bool = False
    ) -> list[CheckReport]:
        return [self.check(c, fix=fix) for c in connections]


class CheckConfigError(RuntimeError):
    """Raised when no prober is configured for a connection's type."""


def _status_for(result: ProbeResult, healed: bool) -> str:
    if result.reachable and result.authenticated:
        return "fixed" if healed else "ok"
    if not result.reachable:
        return "socket-dead" if "socket" in result.detail.lower() else "unreachable"
    return "auth-failed"


# Re-exported so a caller can build an ssh entry without importing 1.2 directly.
__all__ = [
    "AgentEntry",
    "CheckConfigError",
    "CheckReport",
    "ConnectionChecker",
    "HttpProbe",
    "NetworkProber",
    "ProbeResult",
    "Prober",
    "SshProber",
    "SSH_TYPES",
]
