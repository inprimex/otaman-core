# otaman-core

Shared kernel for the Otaman platform — protocols, storage adapters, secrets chain, schemas, and OTel helpers. Nothing lives here unless two or more repos depend on it.

Full documentation, walkthroughs, and architecture notes live at **[docs.otaman.ai](https://docs.otaman.ai)**.

## What this repo owns

- **Storage protocols** — `BusStore`, `AuditStore`, `TranscriptStore`, `SessionStore`.
- **Ownership resolution** — `resolve_agent_identity`: maps a working directory to its declared agent identity.
- **Secret-source chain** — `SecretSource` Protocol with env, dotenv, keyring backends.
- **Auth validators** — bearer token validator shared by bridge and runner.
- **Adapter contract** — `AdapterProtocol` + `AdapterCapabilities` types that all transport adapters implement.
- **Git host integration** — `GitHostAdapter` Protocol + adapters for GitHub, GitLab, Bitbucket, Azure DevOps, Gitea/Forgejo.
- **PM tool sync** — `PmSyncAdapter` Protocol + value types consumed by `otaman-adapters` for Easy8/Redmine/etc.
- **Human roster** — `HumanRosterEntry` dataclass + loader for the `human-roster:` block in `platform.yaml`.
- **CloudEvents helpers** — envelope builders used by bridge and runner.
- **Schemas** — canonical AsyncAPI, OpenAPI, and JSON schemas (including `platform-schema.yaml`) consumed by bridge and CLI.
- **Worktree primitives** — shared types for worktree-based agent isolation.
- **SpecBackend Protocol** — `OpenSpecBackend`, `ADRBackend`, `NoneBackend`; pluggable spec storage.
- **Hook contracts** — pre/post hook interfaces enforced across the harness and plugin.
- **Bus message validator** — frontmatter schema validation for `.agents/bus/` messages.
- **OTel setup** — one-call tracer/meter initialisation used by every service.

## What does NOT go here

Code that is only used by one repo, HTTP servers, deployment config, or harness-specific logic.
The two-consumer rule is the gatekeeping criterion: if fewer than two repos import it, it stays in the owning repo.

## Installation

`otaman-core` is not yet published to PyPI. Install it directly from GitHub:

```bash
pip install "git+https://github.com/inprimex/otaman-core.git"
```

Optional extras pull in backends that are otherwise import-time optional:

```bash
# OS keychain secret backend
pip install "otaman-core[keyring] @ git+https://github.com/inprimex/otaman-core.git"

# OIDC/JWT token validation
pip install "otaman-core[oidc] @ git+https://github.com/inprimex/otaman-core.git"

# tree-sitter code parsing (wiki ingestion)
pip install "otaman-core[wiki] @ git+https://github.com/inprimex/otaman-core.git"
```

Requires Python 3.11+.

## Usage

`otaman-core` is a library of shared primitives, not an application. A few of the
most commonly used public entry points:

**Validate a `platform.yaml`** against the canonical schema:

```python
from pathlib import Path
from otaman_core.validate_platform import load_yaml, validate_builtin

config = load_yaml(Path("platform.yaml"))
errors = validate_builtin(config)          # [] means the config is valid
if errors:
    for e in errors:
        print("invalid:", e)
```

The same check is available as a module:

```bash
python -m otaman_core.validate_platform platform.yaml
```

**Load the human roster** (the `human-roster:` block of a `platform.yaml`):

```python
from pathlib import Path
from otaman_core.human_roster import load_human_roster

roster = load_human_roster(Path("platform.yaml"))
for person in roster:
    print(person.name, person.email, person.roles)
# Each entry is a frozen HumanRosterEntry(name, email, roles, pm_user_id)
```

**Resolve the enforcement identity** for the current working directory (which
agent owns this checkout, for ownership-enforcement decisions):

```python
from otaman_core.identity import resolve_enforcement_identity

identity = resolve_enforcement_identity()   # defaults to the current directory
print(identity)
```

> **API stability:** `otaman-core` is pre-1.0. Modules and types without a
> leading underscore are the intended public surface; underscore-prefixed
> modules (e.g. `otaman_core._resolve`) are internal and may change without
> notice. See [docs.otaman.ai](https://docs.otaman.ai) for the full reference.

## Dependencies

- Python 3.11+
- `uv` (workspace package manager)
- Runtime: `pyyaml`, `jsonschema` (see `pyproject.toml`)
- Optional extras: `keyring` (OS keychain secret backend), `oidc` (JWT validation), `wiki` (tree-sitter code parsing)

## Quick start (development)

```bash
# Install with dev + test extras
uv sync --package otaman-core --extra test

# Run the test suite
uv run --package otaman-core pytest

# Type-check
uv run --package otaman-core mypy src/otaman_core
```

## Repository layout

| Directory | Contents |
|---|---|
| `src/otaman_core/` | Python modules — protocols, adapters, schemas, validators, OTel helpers |
| `src/otaman_core/schemas/` | Canonical JSON/AsyncAPI/OpenAPI schema files consumed by the validators |
| `scripts/` | Operational scripts (audit, vault build, etc.) — not packaged at install time |
| `tests/` | pytest suite, including schema-drift guard fixtures under `tests/fixtures/examples/` |

## See also

- **[docs.otaman.ai](https://docs.otaman.ai)** — full documentation, walkthroughs, architecture notes, and integration guides
- `CONTRIBUTING.md` — contributor workflow
- `SECURITY.md` — security policy and reporting channel

## License

AGPL-3.0 (community edition). Commercial license available for teams that cannot ship source — see [otaman.ai](https://otaman.ai).
