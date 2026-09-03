"""Secret resolution chain for otaman.

Resolves secret references declared in launch-settings.yaml (and later
platform.yaml) through a tiered source chain:

    1. Process env  — variable already set in the shell
    2. dotenv       — secrets.env (gitignored, mode 0600); scoped by ``scope``
                      on the ref: ``program`` (default; <root>/.otaman/secrets.env),
                      ``org`` (~/orgs/<org>/config/secrets.env), or ``tenant``
                      (~/.otaman/secrets.env). ``workspace`` is a legacy alias of
                      ``program``.
    3. keyring      — OS keychain via the keyring package (optional dep)
    4. (post-v1)    — vault / aws-sm / gcp-sm / azure-kv

The chain is READ-only. The single write path is ``upsert_dotenv_secret`` —
used by enroll-time commands (e.g. hitl TOTP) to persist a generated secret to
a dotenv store; the reader sources never mutate.

Beyond single-scope refs, credential config CASCADES across the three dotenv
layers with per-key merge, nearest-scope-wins (program > org > tenant):
:func:`resolve_cascade` resolves one key from its nearest defining layer, and
:func:`credential_provenance` / :func:`credential_layer_paths` expose the
values-free inventory (key → winning layer, layer → file) that the discovery
surfaces render (agent-credential-access 1.1, Q1 ruling 2026-09-03). Absent
layers are skipped silently; values never leave the call site (Q5).

YAML shape accepted (backwards-compatible short form first):

    # Short form
    bot_token_env: OTAMAN_TG_BOT_PERSONAL

    # Long form
    bot_token:
      sources:
        - { type: env,     name: OTAMAN_TG_BOT_PERSONAL }
        - { type: dotenv,  name: OTAMAN_TG_BOT_PERSONAL }
        - { type: keyring, service: otaman, account: tg-personal }

Usage:
    from _secrets import SecretRef, resolve, resolve_or_fail

    ref = SecretRef.from_config(config_value_from_yaml)
    value = resolve(ref, maestro_root=Path("/path/to/workspace"))
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class SecretRef:
    """A reference to a secret, resolved via a source chain."""

    sources: list[dict[str, Any]]

    @classmethod
    def from_config(cls, config: Any) -> SecretRef:
        """Build from a YAML config value.

        Accepts:
          - A plain string: equivalent to ``{ sources: [{ type: env, name: <str> }] }``
            (used by the ``bot_token_env: NAME`` short form).
          - A dict with a ``sources`` list: the long form.
          - A dict without ``sources``: treated as a single-source spec.
        """
        if config is None:
            raise ValueError("SecretRef config is None")
        if isinstance(config, str):
            return cls(sources=[{"type": "env", "name": config}])
        if isinstance(config, dict):
            if "sources" in config:
                sources = config["sources"]
                if not isinstance(sources, list):
                    raise ValueError(
                        f"SecretRef 'sources' must be a list, got {type(sources).__name__}"
                    )
                return cls(sources=[dict(s) for s in sources])
            return cls(sources=[dict(config)])
        raise ValueError(f"Invalid SecretRef config: {config!r}")


class SecretSource(Protocol):
    """A source that can resolve a secret reference spec to a value."""

    type_name: str

    def resolve(self, spec: dict[str, Any], context: dict[str, Any]) -> str | None:
        """Return the secret value, or None if this source can't resolve it."""
        ...


class EnvSource:
    """Read from the process environment."""

    type_name = "env"

    def resolve(self, spec: dict[str, Any], context: dict[str, Any]) -> str | None:
        name = spec.get("name")
        if not name:
            return None
        value = os.environ.get(name)
        return value if value else None


class DotenvSource:
    """Read from a ``secrets.env`` dotenv (0600).

    Three scopes, selected by ``scope`` on the spec:
      - ``program`` (default; ``workspace`` is a legacy alias):
        ``<root>/.otaman/secrets.env`` — the per-workspace store (with the
        pre-1.0 legacy fallback handled below).
      - ``org``: ``~/orgs/<org>/config/secrets.env`` — the per-org store; the
        org name comes from ``context['org']`` or ``spec['org']``.
      - ``tenant``: ``~/.otaman/secrets.env`` — the per-OS-user store, alongside
        ``hitl.yaml``/``edition.yaml``. Used by tenant-scoped refs such as a
        human's TOTP seed (``hitl.yaml`` ``enrollment[<email>].totp_secret_ref``),
        written at enrollment via :func:`upsert_dotenv_secret`.

    These are the same three layers :func:`resolve_cascade` merges; a
    single-scope ref pins one layer, the cascade walks all three nearest-first.
    """

    type_name = "dotenv"

    def resolve(self, spec: dict[str, Any], context: dict[str, Any]) -> str | None:
        name = spec.get("name")
        if not name:
            return None
        scope = spec.get("scope")
        if scope == "tenant":
            dotenv_path = tenant_secrets_path(context.get("home"))
        elif scope == "org":
            org = context.get("org") or spec.get("org")
            if not org:
                return None
            dotenv_path = org_config_secrets_path(org, context.get("home"))
        else:
            maestro_root = context.get("maestro_root")  # legacy: key renamed otaman_root at 1.0
            if not maestro_root:
                return None
            dotenv_path = _program_secrets_path(maestro_root)
        if not dotenv_path.is_file():
            return None
        return _read_dotenv_value(dotenv_path, name)


class KeyringSource:
    """Read from the OS keychain via the ``keyring`` package (optional dep)."""

    type_name = "keyring"

    def resolve(self, spec: dict[str, Any], context: dict[str, Any]) -> str | None:
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError:
            return None
        service = spec.get("service") or "otaman"
        account = spec.get("account") or spec.get("name")
        if not account:
            return None
        try:
            return keyring.get_password(service, account)
        except Exception:
            return None


def _read_dotenv_value(path: Path, key: str) -> str | None:
    """Minimal .env reader — KEY=VALUE per line, # comments, optional quotes."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != key:
            continue
        value = v.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return value if value else None
    return None


def load_dotenv(maestro_root: Path | str) -> dict[str, str]:  # legacy: parameter renamed at 1.0
    """Load all KEY=VALUE pairs from .otaman/secrets.env. Empty dict if absent.

    Falls back to .maestro/secrets.env (legacy: removed at 1.0).
    """
    path = Path(maestro_root) / ".otaman" / "secrets.env"
    if not path.is_file():
        path = Path(maestro_root) / ".maestro" / "secrets.env"  # legacy: .maestro
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        value = v.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out


def tenant_secrets_path(home: Path | str | None = None) -> Path:
    """The tenant dotenv store: ``~/.otaman/secrets.env`` (``home`` injectable)."""
    base = Path(home) if home else Path.home()
    return base / ".otaman" / "secrets.env"


def org_config_secrets_path(org: str, home: Path | str | None = None) -> Path:
    """The org dotenv store: ``~/orgs/<org>/config/secrets.env`` (``home`` injectable)."""
    base = Path(home) if home else Path.home()
    return base / "orgs" / str(org) / "config" / "secrets.env"


def _program_secrets_path(maestro_root: Path | str) -> Path:
    """The program dotenv store ``<root>/.otaman/secrets.env`` (legacy: ``.maestro`` fallback)."""
    root = Path(maestro_root)
    path = root / ".otaman" / "secrets.env"
    if not path.is_file():
        legacy = root / ".maestro" / "secrets.env"  # legacy: .maestro fallback
        if legacy.is_file():
            return legacy
    return path


#: The credential cascade layers, NEAREST scope first (program > org > tenant).
CREDENTIAL_LAYERS: tuple[str, ...] = ("program", "org", "tenant")


def credential_layer_paths(
    *,
    maestro_root: Path | str | None = None,
    org: str | None = None,
    home: Path | str | None = None,
) -> dict[str, Path]:
    """The dotenv file for each APPLICABLE cascade layer, nearest scope first.

    Values-free. A layer is included only when its input is supplied: ``program``
    needs ``maestro_root``, ``org`` needs ``org``; ``tenant`` is always present.
    The path is returned whether or not the file exists — callers checking
    existence get the location to report either way (the discovery verb names
    where each layer's file lives, present or not).
    """
    paths: dict[str, Path] = {}
    if maestro_root is not None:
        paths["program"] = _program_secrets_path(maestro_root)
    if org is not None:
        paths["org"] = org_config_secrets_path(org, home)
    paths["tenant"] = tenant_secrets_path(home)
    return paths


def resolve_cascade(
    key: str,
    *,
    maestro_root: Path | str | None = None,
    org: str | None = None,
    home: Path | str | None = None,
) -> str | None:
    """Resolve one credential key across the cascade, nearest scope winning.

    Walks program → org → tenant (:data:`CREDENTIAL_LAYERS`) and returns the
    value from the first layer that defines ``key``; ``None`` if no applicable
    layer defines it. This is the per-key merge, resolved at the call site —
    absent layers are skipped silently and the value is returned, never logged.
    """
    for path in credential_layer_paths(maestro_root=maestro_root, org=org, home=home).values():
        if path.is_file():
            value = _read_dotenv_value(path, key)
            if value:
                return value
    return None


def credential_provenance(
    *,
    maestro_root: Path | str | None = None,
    org: str | None = None,
    home: Path | str | None = None,
) -> dict[str, str]:
    """Map every cascade key to the layer that WINS it — VALUES-FREE.

    For each key defined in any applicable layer, records the nearest-winning
    layer name (``program`` / ``org`` / ``tenant``). This is the values-free
    view of the per-key merge that the discovery surfaces (CLAUDE.local.md
    block, ``otaman credentials`` verb) render — names and provenance only,
    never a secret value (Q5).
    """
    provenance: dict[str, str] = {}
    # Iterate FARTHEST-first so nearer layers overwrite — leaving each key
    # mapped to its nearest (winning) layer.
    layers = credential_layer_paths(maestro_root=maestro_root, org=org, home=home)
    for layer in reversed(CREDENTIAL_LAYERS):
        path = layers.get(layer)
        if path is None:
            continue
        for key in _dotenv_keys(path):
            provenance[key] = layer
    return provenance


def _dotenv_keys(path: Path) -> set[str]:
    """The KEY names in a dotenv file (values-free; empty if absent/unreadable)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    keys: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        if key:
            keys.add(key)
    return keys


def list_keys(
    *,
    maestro_root: Path | str | None = None,
    org: str | None = None,
    home: Path | str | None = None,
) -> set[str]:
    """Enumerate the NAMES of secrets available in the dotenv backend — VALUES-FREE.

    The ``list_keys()`` seam that ``connections.missing_secret_refs`` and
    ``otaman connection list`` (cli 3.1) consume to badge which ``secret_ref``s
    have a backing key. Reader-on-top / no new storage: it reads key names only,
    NEVER values (Q5), unioned across every applicable cascade layer — program
    (``<maestro_root>/.otaman/secrets.env``, when ``maestro_root`` given), org
    (``~/orgs/<org>/config/secrets.env``, when ``org`` given), and tenant
    (``~/.otaman/secrets.env``).

    The ``env`` and ``keyring`` sources are name-targeted (not enumerable), so
    they do not contribute — the dotenv store is the enumerable default backend.
    """
    keys: set[str] = set()
    for path in credential_layer_paths(maestro_root=maestro_root, org=org, home=home).values():
        keys |= _dotenv_keys(path)
    return keys


def _render_dotenv_value(value: str) -> str:
    """Render a value for a ``KEY=`` line, round-trippable by ``_read_dotenv_value``.

    Quotes only when needed (whitespace / ``#`` / empty). Raises on values the
    minimal reader cannot round-trip (embedded newline or double-quote).
    """
    if "\n" in value or "\r" in value:
        raise ValueError("dotenv value cannot contain a newline")
    if '"' in value:
        raise ValueError("dotenv value cannot contain a double-quote")
    needs_quote = value == "" or value != value.strip() or " " in value or "#" in value
    return f'"{value}"' if needs_quote else value


def upsert_dotenv_secret(path: Path, key: str, value: str) -> None:
    """Insert or update ``KEY=value`` in a dotenv file; atomic, 0600, preserving
    every other key, comment, and blank line.

    The write path for enroll-time secrets (e.g. a human's TOTP seed) — the READER
    sources above never mutate; this is the sole, format-owning writer so callers
    don't hand-roll dotenv escaping. The value touches disk only (0600) and is
    never returned or logged (Q5: values never reach agent context/bus). The
    parent dir is created 0700.
    """
    if not key or "=" in key or any(c.isspace() for c in key):
        raise ValueError(f"invalid dotenv key: {key!r}")
    rendered = f"{key}={_render_dotenv_value(value)}"

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:  # pragma: no cover - non-POSIX
        pass

    existing: list[str] = []
    if path.is_file():
        try:
            existing = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing = []

    out: list[str] = []
    replaced = False
    for raw in existing:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            if stripped.partition("=")[0].strip() == key:
                out.append(rendered)
                replaced = True
                continue
        out.append(raw)
    if not replaced:
        out.append(rendered)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover - non-POSIX
        pass
    os.replace(tmp, path)


_BUILTIN_SOURCES: dict[str, SecretSource] = {
    "env": EnvSource(),
    "dotenv": DotenvSource(),
    "keyring": KeyringSource(),
}


def register_source(source: SecretSource) -> None:
    """Register an additional source (for extensions like vault, aws-sm)."""
    _BUILTIN_SOURCES[source.type_name] = source


def resolve(
    ref: SecretRef,
    *,
    maestro_root: Path | str | None = None,
    org: str | None = None,
    home: Path | str | None = None,
) -> str | None:
    """Walk the source chain; first non-empty value wins.

    ``home`` overrides the tenant home for ``scope: tenant``/``scope: org``
    dotenv refs (defaults to ``Path.home()``); ``org`` supplies the org name for
    ``scope: org`` refs. Both injectable for tests. Returns None if no source
    supplies a value.
    """
    context: dict[str, Any] = {
        "maestro_root": Path(maestro_root) if maestro_root else None,
        "org": org,
        "home": Path(home) if home else None,
    }
    for spec in ref.sources:
        source_type = spec.get("type")
        if not source_type:
            continue
        source = _BUILTIN_SOURCES.get(source_type)
        if source is None:
            continue
        value = source.resolve(spec, context)
        if value:
            return value
    return None


def resolve_or_fail(
    ref: SecretRef,
    *,
    maestro_root: Path | str | None = None,
    org: str | None = None,
    home: Path | str | None = None,
) -> str:
    """Resolve or raise a descriptive error naming every source tried."""
    value = resolve(ref, maestro_root=maestro_root, org=org, home=home)
    if value:
        return value
    tried = ", ".join(_describe_source(s) for s in ref.sources) or "(no sources configured)"
    raise RuntimeError(
        f"Secret could not be resolved. Sources tried (in order): {tried}. "
        f"Populate one via process env, .otaman/secrets.env, or OS keychain."
    )


def _describe_source(spec: dict[str, Any]) -> str:
    t = spec.get("type", "?")
    if t == "keyring":
        return f"keyring:{spec.get('service', 'otaman')}/{spec.get('account', '?')}"
    return f"{t}:{spec.get('name', '?')}"
