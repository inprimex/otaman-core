---
id: otaman-core.otaman_core.git_host.detect_remote_for_repo
title: detect_remote_for_repo
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host.py
source-line: 149
parent: otaman-core.otaman_core.git_host
---

## Docstring

Read ``git remote get-url origin`` in ``repo_dir`` and parse it.

    Returns None if ``repo_dir`` isn't a git working tree, has no
    origin remote, or the URL doesn't parse into a known shape.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
