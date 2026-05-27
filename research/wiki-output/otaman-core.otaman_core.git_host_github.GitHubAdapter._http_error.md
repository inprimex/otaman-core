---
id: otaman-core.otaman_core.git_host_github.GitHubAdapter._http_error
title: _http_error
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host_github.py
source-line: 137
parent: otaman-core.otaman_core.git_host_github.GitHubAdapter
---

## Docstring

Turn a non-success HTTP status into an actionable message.

        The GitHub error body usually has a ``message`` field; surfacing
        it directly lets users see 'Bad credentials' / 'Not Found' /
        'Requires permissions' without digging through logs.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
