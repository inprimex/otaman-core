---
id: otaman-core.otaman_core.wiki.__init__
title: __init__
kind: module
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/wiki/__init__.py
source-line: 1
---

## Docstring

otaman_core.wiki — static-analysis wiki ingestion pipeline (spike).

Extracts L3 (module/component) and L4 (code-unit) entities from Python source
via Tree-sitter and emits deterministic markdown entity files to .otaman/wiki/.

Public surface (spike):
    ingest()       — orchestrator; walk src dirs, emit entity files
    WikiEntity     — entity data type + markdown serializer
    HAS_TREE_SITTER — True when tree-sitter + tree-sitter-python are installed

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
