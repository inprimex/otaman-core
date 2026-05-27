---
id: otaman-core.otaman_core.git_host.validate_token
title: validate_token
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host.py
source-line: 283
parent: otaman-core.otaman_core.git_host
---

## Docstring

Call the provider's whoami/me endpoint with the PAT.

    Intentionally minimal — just proves "this token talks to this API"
    so `otaman doctor` can flag expired / revoked tokens. Each
    provider has its own endpoint shape, so we branch here rather
    than force a common adapter that would only paper over the
    differences.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
