---
id: otaman-core.otaman_core._resolve.find_maestro_root
title: find_maestro_root
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_resolve.py
source-line: 83
parent: otaman-core.otaman_core._resolve
---

## Docstring

Find the otaman workspace root directory.

    Tries the standard resolution chain (marker file → OTAMAN_ROOT/
    MAESTRO_ROOT env → walk-up) from the given path. If that fails and
    the path is inside a linked git worktree, retries from the worktree's
    main repo — where ``.otaman`` / ``.maestro`` (legacy: removed at 1.0) markers live.

    Args:
        start: Directory to start searching from. Defaults to cwd.

    Returns:
        Resolved absolute path to the otaman root, or None if not found.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
