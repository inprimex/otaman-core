---
id: otaman-core.otaman_core.git_host.get_adapter
title: get_adapter
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host.py
source-line: 498
parent: otaman-core.otaman_core.git_host
---

## Docstring

Build a concrete adapter for ``cfg``.

    Resolves the SecretRef once so the adapter holds a ready-to-use
    token. Raises ``GitHostError`` if the token can't be resolved —
    no point constructing an adapter that will fail every call.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
