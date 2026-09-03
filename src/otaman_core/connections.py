"""Connection model + tenant→org→program cascade resolver (agent-credential-access 1.1).

A connection is the thing an agent consumes — "how do I reach + auth to X" —
modeled as ``{name, type, endpoint, secret_ref, ssh_ref, scope}``. Connections
live in a ``connections.yaml`` per scope; this module cascades them
tenant → org → program with per-name merge (the NEAREST scope wins) and exposes
a values-free inventory built on top of the secret backend's ``list_keys()``.

Hard invariant (Q5): this layer surfaces only locations/identifiers —
``secret_ref`` (a backend key NAME) and ``ssh_ref`` (a Host alias / socket
handle). It NEVER reads, stores, or returns a secret value, and does no network
or key I/O. Resolution is a pure read of ``connections.yaml`` files.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a runtime dependency
    yaml = None  # type: ignore[assignment]

#: Cascade scope order, broadest → nearest. The nearest scope wins per name.
SCOPES: tuple[str, ...] = ("tenant", "org", "program")

#: Credential kinds a connection may carry (agent-credential-access 1.2, Q8).
#: How an agent USES the credential — cheap to record now, painful to retrofit.
KINDS: tuple[str, ...] = ("pat", "deploy-key", "api-key", "oauth", "ssh")


class ConnectionsError(ValueError):
    """Raised when a connections.yaml is malformed or missing a required field."""


@dataclass(frozen=True)
class Connection:
    """A resolved connection. All fields are locations/identifiers — never values.

    ``secret_ref`` is a secret-backend key NAME (resolved at the call site, never
    here); ``ssh_ref`` is the ``~/.ssh/config`` ``Host`` entry this resource maps
    to — the external-resource → ssh Host pointer (1.2). The ssh MECHANISM (key
    selection) stays in ``~/.ssh/config``; this layer stores only the pointer and
    metadata, never a key path or value. ``ssh_scope`` is an optional free-text
    note describing the pointer's scope/usage (e.g. "prod deploy, read-only").
    ``kind`` is one of :data:`KINDS`. All may be ``None`` depending on ``type``.
    """

    name: str
    type: str
    endpoint: str
    scope: str  # tenant | org | program
    secret_ref: str | None = None
    ssh_ref: str | None = None
    kind: str | None = None
    ssh_scope: str | None = None


def parse_connections(data: Any, *, default_scope: str = "program") -> list[Connection]:
    """Parse one connections.yaml mapping into :class:`Connection` objects.

    ``default_scope`` labels connections that omit an explicit ``scope`` (they
    take the scope of the file they live in). Raises :class:`ConnectionsError`
    on malformed input.
    """
    if data is None:
        return []
    if not isinstance(data, dict):
        raise ConnectionsError("connections.yaml must be a mapping")
    raw = data.get("connections", [])
    if not isinstance(raw, list):
        raise ConnectionsError("connections: must be a list")

    out: list[Connection] = []
    for i, conn in enumerate(raw):
        if not isinstance(conn, dict):
            raise ConnectionsError(f"connections[{i}]: must be a mapping")
        for field in ("name", "type", "endpoint"):
            if field not in conn:
                raise ConnectionsError(f"connections[{i}]: missing required field '{field}'")
        scope = conn.get("scope", default_scope)
        if scope not in SCOPES:
            raise ConnectionsError(f"connections[{i}].scope: must be one of {', '.join(SCOPES)}")
        kind = conn.get("kind")
        if kind is not None and kind not in KINDS:
            raise ConnectionsError(f"connections[{i}].kind: must be one of {', '.join(KINDS)}")
        out.append(
            Connection(
                name=conn["name"],
                type=conn["type"],
                endpoint=conn["endpoint"],
                scope=scope,
                secret_ref=conn.get("secret_ref"),
                ssh_ref=conn.get("ssh_ref"),
                kind=kind,
                ssh_scope=conn.get("ssh_scope"),
            )
        )
    return out


def resolve(scope_files: Sequence[tuple[str, Path]]) -> list[Connection]:
    """Cascade-merge connections across scopes, per connection name.

    ``scope_files`` is an ordered sequence of ``(scope_label, path)`` from
    broadest to nearest (tenant → org → program). Later scopes override earlier
    ones per name; unrelated connections from broader scopes still resolve.
    Missing files are skipped. Returns the merged inventory sorted by name.

    Pure read-only: reads ``connections.yaml`` files only — no network, no
    secret values.
    """
    if yaml is None:  # pragma: no cover - yaml is a runtime dependency
        raise ConnectionsError("PyYAML is required to resolve connections")
    merged: dict[str, Connection] = {}
    for scope_label, path in scope_files:
        if not path.is_file():
            continue
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        for conn in parse_connections(data, default_scope=scope_label):
            merged[conn.name] = conn  # nearest (later) scope wins per name
    return sorted(merged.values(), key=lambda c: c.name)


def default_scope_files(
    program_root: Path,
    *,
    org_config_dir: Path | None = None,
    home: Path | None = None,
) -> list[tuple[str, Path]]:
    """The standard tenant → org → program ``connections.yaml`` locations.

    - tenant: ``~/.otaman/connections.yaml``
    - org: ``<org_config_dir>/connections.yaml`` (when an org scope applies)
    - program: ``<program_root>/connections.yaml``

    Returned in cascade order for :func:`resolve`. Paths need not exist — absent
    files are skipped by ``resolve``.
    """
    home = home or Path.home()
    files: list[tuple[str, Path]] = [("tenant", home / ".otaman" / "connections.yaml")]
    if org_config_dir is not None:
        files.append(("org", org_config_dir / "connections.yaml"))
    files.append(("program", program_root / "connections.yaml"))
    return files


def resolve_for(
    program_root: Path,
    *,
    org_config_dir: Path | None = None,
    home: Path | None = None,
) -> list[Connection]:
    """Convenience: resolve the standard scope files for a program context."""
    return resolve(default_scope_files(program_root, org_config_dir=org_config_dir, home=home))


def missing_secret_refs(
    connections: Iterable[Connection],
    available_keys: Iterable[str],
) -> list[str]:
    """Values-free inventory check on top of the backend's ``list_keys()``.

    Given the connections and the set of key names the secret backend reports
    via ``list_keys()``, return the names of connections whose ``secret_ref``
    has no backing key. Never touches secret values — compares identifiers only.
    """
    keys = set(available_keys)
    return sorted(
        c.name for c in connections if c.secret_ref is not None and c.secret_ref not in keys
    )
