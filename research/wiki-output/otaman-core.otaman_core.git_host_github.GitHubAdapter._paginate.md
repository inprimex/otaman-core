---
id: otaman-core.otaman_core.git_host_github.GitHubAdapter._paginate
title: _paginate
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host_github.py
source-line: 177
parent: otaman-core.otaman_core.git_host_github.GitHubAdapter
---

## Docstring

Follow ``Link: rel="next"`` headers until exhausted.

        Caps at 20 pages (2000 items at per_page=100) to avoid runaway
        pulls on huge repos; if a real project needs more, the caller
        should filter server-side via ``params`` rather than pulling
        the world.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
