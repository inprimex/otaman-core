# otaman-core

Shared kernel for the Otaman platform — protocols, storage adapters, secrets chain, schemas, and OTel helpers. Nothing lives here unless two or more repos depend on it.

## Status

| Component | Shipped | Roadmap |
|---|---|---|
| `BusStore` / `AuditStore` (SQLite) | shipped | Postgres (Step 3) |
| `TranscriptStore` / `SessionStore` (SQLite) | shipped | Postgres (Step 3) |
| `resolve_agent_identity` | shipped | — |
| `SecretSource` Protocol (env / dotenv / keyring) | shipped | Vault / KMS (Step 4) |
| Bearer token validator | shipped | — |
| OIDC JWKS validator | stubbed | Zitadel wiring (Step 4) |
| Adapter Protocol + `AdapterCapabilities` types | shipped | — |
| NATS client wrappers | — | ADR-006 wiring (Step 4) |
| CloudEvents helpers | shipped | — |
| AsyncAPI / OpenAPI / JSON schemas | shipped | — |
| Worktree primitives | shipped | — |
| `SpecBackend` Protocol (`OpenSpecBackend` / `ADRBackend` / `NoneBackend`) | shipped | — |
| Hook contracts | shipped | — |
| Path resolution helpers | shipped | — |
| OTel setup (traces + metrics) | shipped | — |

## What this repo owns

- **Storage protocols** — `BusStore`, `AuditStore`, `TranscriptStore`, `SessionStore`; SQLite today, Postgres adapter in Step 3.
- **Ownership resolution** — `resolve_agent_identity`: maps a working directory to its declared agent identity.
- **Secret-source chain** — `SecretSource` Protocol with env, dotenv, keyring, and (roadmap) Vault/KMS backends.
- **Auth validators** — bearer token and OIDC JWKS validators shared by bridge and runner.
- **Adapter contract** — `AdapterProtocol` + `AdapterCapabilities` types that all transport adapters implement.
- **NATS/CloudEvents** — client wrappers and CloudEvents envelope builders (NATS transport arrives Step 4).
- **Schemas** — canonical AsyncAPI, OpenAPI, and JSON schemas consumed by bridge and CLI.
- **Worktree primitives** — shared types for worktree-based agent isolation.
- **SpecBackend Protocol** — `OpenSpecBackend`, `ADRBackend`, `NoneBackend`; pluggable spec storage.
- **Hook contracts** — pre/post hook interfaces enforced across the harness and plugin.
- **OTel setup** — one-call tracer/meter initialisation used by every service.

## What does NOT go here

Code that is only used by one repo, HTTP servers, deployment config, or harness-specific logic.
The two-consumer rule is the gatekeeping criterion: if fewer than two repos import it, it stays in the owning repo.
See [polyrepo-structure.md](https://github.com/inprimex/otaman-meta/blob/main/polyrepo-structure.md).

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

## See also

- [polyrepo-structure.md](https://github.com/inprimex/otaman-meta/blob/main/polyrepo-structure.md) — ownership map across all repos
- [phased-roadmap.md](https://github.com/inprimex/otaman-meta/blob/main/phased-roadmap.md) — Step 1–7 sequencing
- [ADR-006 (NATS system bus)](https://github.com/inprimex/otaman-meta/blob/main/adrs/ADR-006-nats-system-bus.md) — future event substrate
- [ADR-010 (user binding + seat licensing)](https://github.com/inprimex/otaman-meta/blob/main/adrs/ADR-010-user-binding-and-seat-licensing.md) — auth model
- [otaman.dev](https://otaman.dev) — platform docs

## License

AGPL-3.0 (community edition). Commercial license available for teams that cannot ship source — see [otaman.dev](https://otaman.dev).
