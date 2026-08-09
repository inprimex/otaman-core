# Changelog

All notable changes to `otaman-core` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `runner.agent_bootstrap.plugin_dir` field in `platform-schema.yaml`.
- `standards.git.environments` and `standards.git.merge_policy` schema blocks
  (git-flow / branch-environment configuration), including `tag_pattern` match key.
- Contributor-facing project files: `CODE_OF_CONDUCT.md`, `CHANGELOG.md`,
  issue/PR templates, and `CODEOWNERS`.

### Changed
- CI now enforces `ruff`, `mypy`, package build, `twine check`, and an import
  smoke test across Python 3.11–3.13 on Linux, macOS, and Windows.
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

[Unreleased]: https://github.com/inprimex/otaman-core/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/inprimex/otaman-core/releases/tag/v0.2.0
