---
id: otaman-core.otaman_core._resolve._safe_marker_path
title: _safe_marker_path
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_resolve.py
source-line: 55
parent: otaman-core.otaman_core._resolve
---

## Docstring

Return False and warn if *rel* from *marker* is unsafe.

    Rejects paths with more than 3 ``..`` components (traversal bound) or that
    resolve to a location outside the user's home directory.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
