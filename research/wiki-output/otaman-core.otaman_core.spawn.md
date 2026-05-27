---
id: otaman-core.otaman_core.spawn
title: spawn
kind: module
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/spawn.py
source-line: 1
---

## Docstring

Spawn types — shared by otaman-runner and the CLI fallback path.

Defines the data model used to request a session spawn, describe the
result, and back the ``TerminalBackend`` Protocol that runner
implementations target.

Per ADR-009: ``otaman-runner`` is the unified spawner. These types live
in ``otaman-core`` so the Mode 1 (solo) CLI fallback can use the same
spawn library function without depending on the runner package.

Out of scope for v0 (deferred per Team-Mode Pilot decision, 2026-05-14):
    - ``HeadlessBackend`` / ``HybridBackend`` (pilot is all interactive)
    - ``WindowsTerminalBackend`` / ``ScreenBackend`` (pilot server is Linux)
    - NATS event publication (Mode 2 stays file-bus + audit log for v0)

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
