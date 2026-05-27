---
id: otaman-core.otaman_core._resolve
title: _resolve
kind: module
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_resolve.py
source-line: 1
---

## Docstring

Shared otaman workspace root resolution for all Python scripts.

Resolution chain (first match wins):
1. .otaman (preferred) or .maestro (legacy: removed at 1.0) marker file in start dir or ancestors
2. OTAMAN_ROOT (preferred) or MAESTRO_ROOT (legacy: removed at 1.0) environment variable
3. Walk-up fallback: look for platform.yaml or .agents/ (legacy/monorepo compat)

Also exposes expand_config_dir() for per-shell tilde / env-var expansion of
account config_dir paths declared in launch-settings.yaml.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
