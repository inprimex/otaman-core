# otaman-core

> **Otaman platform:** **otaman-core (you are here)** · [otaman-cli](https://github.com/inprimex/otaman-cli) · [otaman-plugin](https://github.com/inprimex/otaman-plugin) · [otaman-bridge](https://github.com/inprimex/otaman-bridge) · [otaman-runner](https://github.com/inprimex/otaman-runner) · [otaman-adapters](https://github.com/inprimex/otaman-adapters)

Shared Python kernel for the Otaman platform — workspace/identity resolution, the
secret-source chain, credential/connection primitives, config schemas, git-host
adapters, and bus contracts. Nothing lives here unless two or more repos depend
on it (the two-consumer rule).

Full documentation, walkthroughs, and architecture notes live at **[docs.otaman.ai](https://docs.otaman.ai)**.

## What this repo owns

- **Workspace & identity resolution** — `resolve_enforcement_identity` (which agent
  owns a checkout), project-root/worktree resolution (`_resolve`), and per-path
  ownership within a repo (`owner_paths`).
- **Secret-source chain** — `SecretSource` Protocol with env, dotenv, and keyring
  backends (`resolve` / `resolve_or_fail`); tenant- and workspace-scoped dotenv;
  a 0600 write helper (`upsert_dotenv_secret`); and a values-free `list_keys`
  inventory.
- **Connections & credential access** — the `Connection` model with a
  tenant→org→program cascade resolver (`connections`), a per-target ssh-agent
  socket registry (`ssh_registry`), and a reachability/auth check engine with a
  persisted, values-free `CheckReport` store (`connection_check`).
- **CE web-auth** — `CeAuthManager` (login / attach-token / JWT issue+verify),
  the one host-agnostic auth service mounted by the bridge (CE) and re-mounted by
  the runner (EE).
- **OIDC/JWT validation** — `OIDCValidator` parses and verifies
  `Authorization: Bearer` tokens (`auth_oidc`).
- **Program lifecycle registry** — `read_program_state` is the single read point
  for every per-org service (runner, bridge, fswatch, router, CLI); with an
  append-history writer (`record_transition`) and doctor checks (`lifecycle`).
- **Identity lock** — an `flock(2)`-based single-acting-session guard
  (`acting_lock`: `acquire` / `probe` / `lock_key`), kernel-released on exit.
- **Human roster** — `HumanRosterEntry` + loader for the `human-roster:` block of
  `platform.yaml`, the well-known `approver` role, and eligibility resolution
  (`human_roster`).
- **Git-host integration** — `GitHostAdapter` Protocol + `RepoInfo` and adapters
  for GitHub, GitLab, Bitbucket, Azure DevOps, and Gitea/Forgejo.
- **PM-tool sync** — a PM-adapter `Protocol` + value types
  (`PmAdapterCapabilities`, `PmSyncConfig`) consumed by `otaman-adapters`.
- **Config validation & schemas** — `validate_platform` (+ `validate_connections`)
  and the canonical JSON schemas under `schemas/` (`platform-schema.yaml`,
  `connections-schema.yaml`, `hitl-schema.yaml`).
- **Bus contracts** — frontmatter/type validation for `.agents/bus/` messages
  (`validate_message`), bus URI addressing (`bus.uri`), and the HITL confirmation
  ledger (`confirmations`).
- **Spawn & test primitives** — the session-spawn data model shared by the runner
  and the CLI fallback (`spawn`), and the bus-test-isolation fixtures
  (`testing`: `isolate_bus`, `make_program_sandbox`).

## What does NOT go here

Code that only one repo uses, HTTP servers, deployment config, or harness-specific
logic. The two-consumer rule is the gatekeeping criterion: if fewer than two repos
import it, it stays in the owning repo.

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

# CE web-auth (login/attach-token/JWT + bcrypt)
pip install "otaman-core[web-auth] @ git+https://github.com/inprimex/otaman-core.git"

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
errors = validate_builtin(config)  # [] means the config is valid
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
# Each entry is a frozen HumanRosterEntry(name, email, roles, pm_user_id);
# email is optional (str | None).
```

**Resolve the enforcement identity** for the current working directory (which
agent owns this checkout, for ownership-enforcement decisions):

```python
from otaman_core.identity import resolve_enforcement_identity

identity = resolve_enforcement_identity()  # defaults to the current directory
print(identity)
```

> **API stability:** `otaman-core` is pre-1.0. Modules and types without a
> leading underscore are the intended public surface; underscore-prefixed
> modules (e.g. `otaman_core._resolve`, `otaman_core._secrets`) are internal and
> may change without notice. See [docs.otaman.ai](https://docs.otaman.ai) for the
> full reference.

## Dependencies

- Python 3.11+
- `uv` (workspace package manager)
- Runtime: `pyyaml`, `jsonschema` (see `pyproject.toml`)
- Optional extras: `keyring` (OS keychain secret backend), `oidc` (JWT
  validation), `web-auth` (CE login/attach-token/JWT), `wiki` (tree-sitter code
  parsing)

## Quick start (development)

```bash
# Install with dev + test extras
uv sync --extra test

# Run the test suite
uv run pytest

# Lint + format check
uv run ruff check .
uv run ruff format --check .

# Type-check
uv run mypy src/otaman_core
```

CI runs lint, format-check, mypy, the test suite, a package build, and an import
smoke test across Python 3.11–3.13 on Linux, macOS, and Windows.

## Repository layout

| Directory | Contents |
|---|---|
| `src/otaman_core/` | Python modules — resolvers, protocols, adapters, validators |
| `src/otaman_core/schemas/` | Canonical JSON schemas (e.g. `platform-schema.yaml`) consumed by the validators |
| `scripts/` | Operational scripts (audit, etc.) — not packaged at install time |
| `tests/` | pytest suite, including schema-drift fixtures under `tests/fixtures/examples/` |

## See also

- **[docs.otaman.ai](https://docs.otaman.ai)** — full documentation, walkthroughs, architecture notes, and integration guides
- `CONTRIBUTING.md` — contributor workflow
- `SECURITY.md` — security policy and reporting channel

## License

AGPL-3.0-only (Community Edition). Commercial and dual licenses are available from
Inprimex Lab LLC — see [otaman.ai](https://otaman.ai) or contact licensing@inprimex.com.
