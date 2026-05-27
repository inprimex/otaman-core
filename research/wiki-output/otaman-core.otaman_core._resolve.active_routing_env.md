---
id: otaman-core.otaman_core._resolve.active_routing_env
title: active_routing_env
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_resolve.py
source-line: 384
parent: otaman-core.otaman_core._resolve
---

## Docstring

Read the active routing name from environment.

    Resolution order (most preferred first):
      1. ``OTAMAN_ACTIVE_ROUTING`` — current name (set by launcher).
      2. ``OTAMAN_ACTIVE_ACCOUNT`` — pre-rename otaman legacy.
      3. ``MAESTRO_ACTIVE_ACCOUNT`` — pre-rebrand legacy.  # legacy: MAESTRO_ACTIVE_ACCOUNT removed at 1.0

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
