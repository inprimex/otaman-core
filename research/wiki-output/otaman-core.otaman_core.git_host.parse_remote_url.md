---
id: otaman-core.otaman_core.git_host.parse_remote_url
title: parse_remote_url
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host.py
source-line: 90
parent: otaman-core.otaman_core.git_host
---

## Docstring

Classify a remote URL. Returns None if the URL doesn't parse.

    Accepts the three common shapes (legacy SSH, SSH URL, HTTPS) plus
    Azure DevOps's quirky ``/_git/`` path. Silent on garbage input so
    callers can use this for best-effort classification.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
