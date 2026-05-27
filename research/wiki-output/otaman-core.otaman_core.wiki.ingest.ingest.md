---
id: otaman-core.otaman_core.wiki.ingest.ingest
title: ingest
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/wiki/ingest.py
source-line: 13
parent: otaman-core.otaman_core.wiki.ingest
---

## Docstring

Walk Python files in *src_dirs*, extract entities, write to *wiki_dir*.

    Args:
        src_dirs: directories to recurse into.
        wiki_dir: output directory (created if absent).
        repo_name: short repo identifier used as entity-id prefix (e.g. ``"otaman-core"``).
        src_root: root used to compute relative paths for entity ids; if None, each
            src_dir is treated as the root.
        overwrite: if False (default), skip files that already exist so human edits
            and LLM-managed regions are preserved.

    Returns:
        Stats dict with keys ``files``, ``entities``, ``loc``, ``elapsed_sec``,
        ``loc_per_sec``, ``skipped``.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
