# Changelog

All notable changes to `otaman-core` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **spec-approved transition validation** (`interactive-human-console` 2.2):
  `spec_lifecycle.validate_spec_approved_transition` (the console mints the
  stage, core validates it — `authored` predecessor, eligible approver, research
  rejected) plus the symmetric `apply_spec_approved` writer. Composes the SLE
  substrate; `TransitionValidation` result type.
- **Spec lifecycle substrate** (`spec-lifecycle-enforcement` 1.1-1.4, Roman
  Q1-Q9 rulings): `spec_lifecycle.py` — the stage machine (`STAGES`
  pre-proposal→…→archived, `spec-approved` HITL stage, research exemption,
  amendment re-entry), repo-is-truth `.openspec.yaml` `read_stage`/`set_stage`
  (D1), the `SpecPolicy` config layer with org→program cascade + `process.level`
  (L1 never heavier, R1 scale-adaptive; D7), the three **local** gates
  (`check_merge_gate`/`check_dispatch_gate`/`check_archive_gate` → `GateDecision`,
  modes block|warn|self-waive with visible self-waive, per-program outcome-link
  check; D2/D6), and the ratify backend (`ratify`/`apply_ratification`/
  `ratifications_in_month`; human-only, mandatory reason, `ratified: true`; D4).
  All CE-runnable with zero CI/git-host dependency; surfaces (verbs, doctor, CI
  template) live in cli/plugin.
- **Credential cascade** (`agent-credential-access` 1.1, Roman Q1-Q8 rulings):
  per-key merge across the three dotenv layers — program (`<root>/.otaman/`),
  org (`~/orgs/<org>/config/`), tenant (`~/.otaman/`) — nearest scope wins.
  `_secrets.resolve_cascade` resolves one key at the call site;
  `credential_provenance` / `credential_layer_paths` expose the values-free
  key→layer / layer→file inventory the discovery surfaces render. `DotenvSource`
  gains a `scope: org` layer; `resolve()` takes an `org`; `list_keys()` unions
  all three layers. The org layer is auto-discovered from a program root's
  `~/orgs/<org>/…` layout via `resolve_org_root`, so a program cwd alone
  surfaces the org row (aca 1.5 gate fix — the org layer was previously dropped
  whenever the caller could not name the org).
- **Typed connections + ssh Host pointer** (`agent-credential-access` 1.2):
  `Connection` gains `kind` (`pat|deploy-key|api-key|oauth|ssh`, Q8) and an
  `ssh_scope` note on the external-resource → ssh Host pointer (`ssh_ref`). The
  ssh mechanism stays in `~/.ssh/config`; `ssh_registry.ssh_config_has_host`
  and `connection_check.dangling_ssh_hosts` validate the pointed Host exists,
  and `connection check` now fails **naming** the dangling Host — `SshProber`
  defaults `ssh_config_path` to `~/.ssh/config` (via `default_ssh_config_path`)
  so Host validation runs without caller wiring; pass `ssh_config_path=None` to
  opt out. No external SSH-management system in v1 (Q4). `connections-schema.yaml`
  adds both fields (optional, backward-compatible).
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
