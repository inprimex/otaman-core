---
id: otaman-core.otaman_core._resolve.resolve_worktree_main
title: resolve_worktree_main
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_resolve.py
source-line: 183
parent: otaman-core.otaman_core._resolve
---

## Docstring

If ``path`` is inside a git worktree, return the main repo's working tree.

    A linked worktree has a ``.git`` *file* (not directory) at its root. The
    file contains a single line ``gitdir: <path>`` pointing into the main
    repo's ``.git/worktrees/<name>/`` directory. From that gitdir we can
    recover the main repo's working tree as the great-grandparent.

    Walks up from ``path`` looking for any ``.git`` entry:

    - ``.git`` is a regular file → worktree marker, parse and resolve
    - ``.git`` is a directory     → ordinary repo (not a worktree)
    - ``.git`` not found          → not a git working tree at all

    Returns the absolute Path to the main repo's working tree, or ``None``
    when ``path`` is not inside a worktree (including when it's inside the
    main repo itself, or not in any repo). Defensive against malformed
    ``.git`` files: parse failures return ``None`` rather than raising.

    Used by :func:`find_maestro_root` so that hooks fired from inside a  # legacy: find_maestro_root renamed at 1.0
    linked worktree can still locate the otaman folder via the main
    repo's ``.otaman`` / ``.maestro`` marker.  # legacy: .maestro fallback removed at 1.0

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
