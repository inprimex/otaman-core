# otaman-core — working in this repository

`otaman-core` is the shared Python **kernel** for the Otaman platform: storage
protocols, the canonical `platform.yaml` schema, workspace/identity resolution,
the secret-source chain, bus addressing, auth validators, and hook/adapter
Protocols. Code belongs here only when **two or more** components depend on it
(the two-consumer rule); anything used by a single component stays in that
component.

## Development

Requires **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra test              # install with dev + test extras
uv run pytest                     # run the test suite
uv run ruff check .               # lint  (ce-lint-standard baseline)
uv run ruff format --check .      # format check
uv run mypy src/otaman_core       # type-check
uv build                          # build the wheel/sdist
```

CI runs lint, format-check, mypy, the test suite, a package build, and an import
smoke test across **Python 3.11–3.13 on Linux, macOS, and Windows**. A change
must pass the full CI gate to merge.

## Repository layout

| Path | Contents |
|---|---|
| `src/otaman_core/` | Kernel modules — protocols, adapters, resolvers, validators, OTel helpers |
| `src/otaman_core/schemas/` | Canonical JSON/AsyncAPI/OpenAPI schemas, incl. `platform-schema.yaml` |
| `scripts/` | Operational scripts (not packaged at install time) |
| `tests/` | pytest suite, including schema-drift fixtures under `tests/fixtures/examples/` |

## Contracts

The schemas under `src/otaman_core/schemas/` and the module-level `Protocol`
types are shared contracts: changes to them ripple to every consuming component.
Keep them **backward-compatible**, add fixture coverage for schema changes, and
call out contract changes explicitly in the PR description.

## Conventions

- Branch per change; all changes land via pull request against `main`.
- Conventional-commit style messages; sign off commits (`git commit -s`) per
  `CONTRIBUTING.md`.
- Include tests for behavioural changes; update `README.md` / `CHANGELOG.md`
  when public API or behaviour changes.

## For AI assistants / automated contributors

Follow `CONTRIBUTING.md`, keep each change focused, include tests, and make sure
the full CI gate (ruff + format + mypy + pytest + build) passes before proposing
a merge. Do not add secrets, credentials, or personal data.

## See also

- `README.md` — overview, install, and usage examples
- `CONTRIBUTING.md` — contribution workflow and license-of-contributions
- `SECURITY.md` — vulnerability reporting
- **[docs.otaman.ai](https://docs.otaman.ai)** — full platform documentation

---

> **Note for platform operators:** running `otaman init` writes the private
> orchestration rules (fleet layout, bus internals) to a **gitignored
> `CLAUDE.local.md`** in your working tree, which Claude Code auto-loads after
> this file. That file is never committed; this committed guide is the
> public-safe entry point. Re-run `otaman init` to refresh the local rules.
