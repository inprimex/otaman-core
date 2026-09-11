# Changelog

All notable changes to `otaman-core` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`spec-approval-pending` message type** (`spec-gate-hardening` 1.4 support):
  added to `validate_message.VALID_TYPES` — the targeted (`to: human`) triage
  item `otaman propose` enqueues for the human's awaiting-approval queue and
  `otaman check` surfaces. Not a broadcast. Unblocks the cli writer (which is
  bwsv-pre-write-gated and would otherwise reject the unknown type).
- **`x-gate-waived` frontmatter field** (`spec-gate-hardening` 1.5): `validate_message`
  now admits and validates the optional `x-gate-waived: <violation-slug>` field —
  written only by the gate-waiver path to record, per action, which spec-lifecycle
  gate violation was waived (kebab-case slug). Landing it in the validator first
  lets the bus-writer pre-write gate admit it instead of rejecting it as unknown.
- **Write-time message-validation gate** (`bus-writer-self-validation` 1.1):
  `validate_message.validate_message_before_write(content)` — the errors-only
  library call bus writers invoke on a rendered message BEFORE writing it, so
  the platform never persists a message its own validator would reject. Same
  rules as `validate_message` (incl. the broadcast-type table); empty list =
  safe to write.

### Fixed
- **`spec-change-approved` is a valid broadcast** (`tenant-defect-report` B4):
  added `spec-change-approved` to `validate_message._BROADCAST_TYPES` — the
  human's SCR-approval broadcast (`approve.py` → `to: all`) was rejected by the
  CLI's own validator, making every approval audit record invalid. Shared
  contract; the cli mirror list should stay in sync.

### Added
- **Delivery mode + gate-gated auto-archive** (`console-lifecycle-actions` 2.1):
  `spec_lifecycle` gains `delivery: hitl|auto` (`read_delivery`/`resolve_delivery`,
  absent = hitl), a `spec_policy.delivery_default` cascade key, and
  `auto_archive_decision`/`apply_auto_archive` — the automatic verified→archived
  transition runs ONLY when delivery resolves to `auto`, the stage is `verified`,
  and the archive gate passes CLEANLY (allowed and NOT waived; a warn/self-waive
  is not a pass), recorded like any archive (D3/D4). Console rendering stays cli's.
- **Constitutional gate Stage-1 lint + critic telemetry** (`spec-proposal-constitutional-gate`
  1.1/1.4, JTBD-57 Hook A): `spec_gate.py` — `lint_proposal` runs the deterministic,
  never-blocking checks (front-matter schema, resolvable outcome/citations,
  gitleaks-lite secret scan on the body, no TBD/TODO in critical fields,
  `affected_repos` vs platform.yaml) and emits a 0-100 `LintResult` (score + tier
  + values-free `LintFinding`s); `scan_secrets` returns pattern codes only, never
  the secret. `record_critic_cost`/`total_critic_cost` capture per-critic-invocation
  cost telemetry (pass-cap ≤2 enforced) for the platform usage plumbing (D6). Stage-2
  critic and surfaces are plugin/cli.
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
