"""Thin multi-target ssh-agent socket registry (agent-credential-access 1.2).

The DEFAULT ssh backend in every edition. Stock ``ssh-agent`` gives you one
socket per shell and loses it on context reset; this registry runs one agent
PER TARGET and persists the ``target → {key, socket, pid}`` map under
``XDG_RUNTIME_DIR`` so an agent can re-attach the correct socket after its
``SSH_AUTH_SOCK`` is lost — by reading the map, not the environment.

Liveness is probed with ``ssh-add -l`` against a candidate socket (exit 0/1 =
agent reachable, 2 = dead). Spawning an agent / loading a key are thin
primitives the 1.3 check engine drives behind ``--fix``/``--reattach``.

Hard invariant (Q5): this layer handles LOCATIONS only — a socket path, a pid,
and a ``key`` that is a locator (a private-key PATH or a ``~/.ssh/config`` Host
alias), NEVER key material. It never reads, stores, or returns a private key's
contents. ``ssh-add`` is handed a path and manages the bytes itself.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

#: A command runner: takes argv + an env overlay, returns the completed process.
#: Injected so the registry is unit-testable without a real ssh-agent.
CommandRunner = Callable[[list[str], dict[str, str]], "subprocess.CompletedProcess[str]"]

REGISTRY_FILENAME = "ssh-agents.json"


def _default_runner(
    argv: list[str], env_overlay: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_overlay}
    return subprocess.run(argv, env=env, capture_output=True, text=True, check=False)


@dataclass(frozen=True)
class AgentEntry:
    """One target's per-target ssh-agent coordinates. All locators — never key bytes.

    ``key`` is a private-key PATH or a ``~/.ssh/config`` Host alias (a pointer),
    ``socket`` is the agent's ``SSH_AUTH_SOCK`` path, ``pid`` the agent process.
    """

    target: str
    key: str
    socket: str
    pid: int | None = None


def default_runtime_dir() -> Path:
    """The base dir for the persisted map: ``$XDG_RUNTIME_DIR/otaman`` (fallback
    ``~/.otaman/run``). Not created here — :meth:`SshAgentRegistry.save` does."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg) if xdg else Path.home() / ".otaman" / "run"
    return base / "otaman"


class SshAgentRegistry:
    """Persistent ``target → AgentEntry`` map plus liveness / re-attach primitives.

    The map is JSON under ``<runtime_dir>/ssh-agents.json`` (dir 0700, file
    0600 — socket paths aren't secret but we keep them owner-only). All shelling
    out goes through the injected ``runner`` so the registry is testable without
    a live agent.
    """

    def __init__(
        self,
        runtime_dir: Path | None = None,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self._runtime_dir = runtime_dir or default_runtime_dir()
        self._runner: CommandRunner = runner or _default_runner
        self._entries: dict[str, AgentEntry] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._runtime_dir / REGISTRY_FILENAME

    # ---- persistence ------------------------------------------------------

    def load(self) -> dict[str, AgentEntry]:
        """Read the persisted map (empty if absent/unreadable). Idempotent."""
        self._entries = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            for target, rec in (raw or {}).items():
                if isinstance(rec, dict) and "socket" in rec and "key" in rec:
                    self._entries[target] = AgentEntry(
                        target=target,
                        key=rec["key"],
                        socket=rec["socket"],
                        pid=rec.get("pid"),
                    )
        self._loaded = True
        return dict(self._entries)

    def save(self) -> None:
        """Persist the map with owner-only perms (dir 0700, file 0600)."""
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._runtime_dir, 0o700)
        except OSError:  # pragma: no cover - non-POSIX
            pass
        payload = {
            t: {k: v for k, v in asdict(e).items() if k != "target"}
            for t, e in self._entries.items()
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - non-POSIX
            pass
        os.replace(tmp, self.path)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ---- map access -------------------------------------------------------

    def entries(self) -> dict[str, AgentEntry]:
        self._ensure_loaded()
        return dict(self._entries)

    def get(self, target: str) -> AgentEntry | None:
        self._ensure_loaded()
        return self._entries.get(target)

    def register(self, entry: AgentEntry) -> None:
        """Record (or replace) a target's entry and persist."""
        self._ensure_loaded()
        self._entries[entry.target] = entry
        self.save()

    def remove(self, target: str) -> bool:
        self._ensure_loaded()
        existed = self._entries.pop(target, None) is not None
        if existed:
            self.save()
        return existed

    # ---- liveness / re-attach --------------------------------------------

    def is_live(self, entry: AgentEntry) -> bool:
        """True if an ssh-agent is reachable at ``entry.socket``.

        Probes with ``ssh-add -l`` and ``SSH_AUTH_SOCK`` pointed at the socket:
        exit 0 (has keys) or 1 (reachable, no keys) → live; 2 (cannot connect)
        or missing socket → dead.
        """
        if not entry.socket or not Path(entry.socket).exists():
            return False
        proc = self._runner(["ssh-add", "-l"], {"SSH_AUTH_SOCK": entry.socket})
        return proc.returncode in (0, 1)

    def reattach(self, target: str) -> AgentEntry | None:
        """Re-attach the correct socket for ``target`` from the persisted map.

        Returns the entry when it exists AND its socket is live — the
        env-independent recovery path after a context reset. Returns ``None``
        when unknown or dead (1.3's ``--fix`` respawns in that case).
        """
        entry = self.get(target)
        if entry is None:
            return None
        return entry if self.is_live(entry) else None

    def prune_dead(self) -> list[str]:
        """Drop entries whose socket is no longer live; return removed targets."""
        self._ensure_loaded()
        dead = [t for t, e in self._entries.items() if not self.is_live(e)]
        for t in dead:
            self._entries.pop(t, None)
        if dead:
            self.save()
        return dead

    # ---- thin spawn / load primitives (driven by 1.3 --fix) ---------------

    def spawn_agent(self, target: str, key: str, socket: str | None = None) -> AgentEntry:
        """Start a per-target ``ssh-agent`` bound to a fixed socket and register it.

        ``key`` is a locator recorded on the entry (path or Host alias) — this
        method does NOT load it (see :meth:`load_key`). The socket defaults to
        ``<runtime_dir>/agent-<target>.sock``. Raises on spawn failure.
        """
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        sock = socket or str(self._runtime_dir / f"agent-{target}.sock")
        # -a binds a fixed socket path; without -s/-c ssh-agent prints env lines.
        proc = self._runner(["ssh-agent", "-a", sock], {})
        if proc.returncode != 0:
            raise SshRegistryError(
                f"ssh-agent spawn failed for target '{target}': {proc.stderr.strip()}"
            )
        pid = _parse_agent_pid(proc.stdout)
        entry = AgentEntry(target=target, key=key, socket=sock, pid=pid)
        self.register(entry)
        return entry

    def load_key(self, target: str) -> bool:
        """Load the target's key into its agent via ``ssh-add <key-path>``.

        Hands ``ssh-add`` the key PATH only; the private bytes never enter this
        process. Returns True on success. Raises if the target isn't registered.
        """
        entry = self.get(target)
        if entry is None:
            raise SshRegistryError(f"target '{target}' is not registered")
        proc = self._runner(["ssh-add", entry.key], {"SSH_AUTH_SOCK": entry.socket})
        return proc.returncode == 0


class SshRegistryError(RuntimeError):
    """Raised on ssh-agent spawn / registry operation failure."""


def _parse_agent_pid(stdout: str) -> int | None:
    """Extract the pid from ``ssh-agent`` env output (``SSH_AGENT_PID=1234; ...``)."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("SSH_AGENT_PID="):
            frag = line[len("SSH_AGENT_PID=") :]
            frag = frag.split(";", 1)[0].strip()
            if frag.isdigit():
                return int(frag)
    return None


def ssh_config_identity(host_alias: str, config_path: Path) -> str | None:
    """Resolve a ``~/.ssh/config`` Host alias to its ``IdentityFile`` locator.

    Thin, dependency-free parse: walks ``Host`` stanzas and returns the first
    ``IdentityFile`` under a stanza whose patterns match ``host_alias`` exactly.
    Returns a PATH (a locator) or ``None`` — never key material. Used to turn a
    connection's ``ssh_ref`` (Host alias) into the key path for :meth:`spawn_agent`.
    """
    if not config_path.is_file():
        return None
    in_match = False
    for raw in config_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        keyword, value = parts[0].lower(), parts[1].strip()
        if keyword == "host":
            patterns = value.split()
            in_match = host_alias in patterns
        elif keyword == "identityfile" and in_match:
            return os.path.expanduser(value)
    return None


def ssh_config_has_host(host_alias: str, config_path: Path) -> bool:
    """Return whether ``~/.ssh/config`` declares a ``Host`` stanza for ``host_alias``.

    The existence primitive behind ``connection check``'s dangling-pointer
    validation (agent-credential-access 1.2): a connection's ``ssh_ref`` points
    at an ssh_config ``Host`` entry, and the check fails when that Host is
    absent. Exact-match on the stanza patterns, mirroring
    :func:`ssh_config_identity`; a missing/unreadable config yields ``False``.
    Reads Host lines only — never key material.
    """
    if not config_path.is_file():
        return False
    for raw in config_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        keyword, value = parts[0].lower(), parts[1].strip()
        if keyword == "host" and host_alias in value.split():
            return True
    return False
