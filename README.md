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

## Dependencies

- Python 3.11+
- `uv` (workspace package manager)
- SQLite (bundled) — Postgres driver optional at import time
- `opentelemetry-sdk`, `cloudevents`, `pydantic` (pinned in `pyproject.toml`)

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
| `archive/` | Internal-only material kept in-tree for project history (not user-facing) |

## See also

- **[docs.otaman.ai](https://docs.otaman.ai)** — full documentation, walkthroughs, architecture notes, and integration guides
- `CONTRIBUTING.md` — contributor workflow
- `SECURITY.md` — security policy and reporting channel

## License

AGPL-3.0 (community edition). Commercial license available for teams that cannot ship source — see [otaman.dev](https://otaman.dev).
