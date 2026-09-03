# Changelog

All notable changes to `otaman-core` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Plugin-tree wiring doctor check** (`ce-bootstrap-plugin-wiring` 1.2):
  `plugin_wiring.py` surfaces both halves of the silent slash-command gap as
  `otaman doctor` WARNs — a vendored plugin tree present while
  `runner.agent_bootstrap.plugin_dir` is absent, and a `plugin_dir` set to a
  missing directory. Pure `check_plugin_wiring` rule plus the disk-facing
  `resolve_plugin_wiring` helper for the CLI wrapper.

## [0.3.0] - 2026-08-26

### Added
- **Connection subsystem** (`agent-credential-access`): tenant→org→program
  connection cascade resolver (`connections.py`), the multi-target ssh-agent
  socket registry (`ssh_registry.py`), and the connection check engine with
  read-only reporting + `--fix` self-heal (`connection_check.py`), plus a
  persisted, program-keyed `CheckReport` store for compaction-durable
  `last-check`. All surfaces are values-free (locations/refs only).
- **Secret chain**: tenant-scoped dotenv resolution (`scope: tenant` →
  `~/.otaman/secrets.env`), a 0600 `upsert_dotenv_secret` writer, and the
  values-free `list_keys()` inventory seam.
- **HITL config**: two-scope `hitl.yaml` schema (`hitl-schema.yaml`) with the
  program no-weakening rule, and the layered `connections.yaml` schema with a
  shared `secret_backend` key.
- Bus URI addressing (`otaman://…`) and the `bus.boundaries` schema.
- `runner.agent_bootstrap.plugin_dir` field in `platform-schema.yaml`.
- `standards.git.environments` and `standards.git.merge_policy` schema blocks
  (git-flow / branch-environment configuration), including `tag_pattern` match key.
- Contributor-facing project files: `CODE_OF_CONDUCT.md`, `CHANGELOG.md`,
  issue/PR templates, and `CODEOWNERS`.

### Changed
- CI now enforces `ruff`, `mypy`, package build, `twine check`, an import
  smoke test, and a `maestro`-reference audit across Python 3.11–3.13 on
  Linux, macOS, and Windows, gated by a `ci-ok` aggregate check.
- Adopted the `ce-lint-standard` baseline (`ruff` lint + format).
- `LICENSE` and `CONTRIBUTING.md` updated to the legal entity **Inprimex Lab LLC**.
- Security contact moved to a project-scoped address (`security@otaman.ai`).
- Documentation dependencies and Python version aligned with `pyproject.toml`
  (Python 3.11+).

### Removed
- `archive/` internal-only material removed from the repository.

## [0.2.0] - 2026-06-28

Initial tagged release of the Otaman platform kernel: storage protocols,
ownership resolution, secret-source chain, schemas, hook contracts, OpenTelemetry
helpers, and the spec-backend protocol.

[Unreleased]: https://github.com/inprimex/otaman-core/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/inprimex/otaman-core/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/inprimex/otaman-core/releases/tag/v0.2.0
