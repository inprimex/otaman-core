---
id: otaman-core.otaman_core.git_host_gitlab._to_pr
title: _to_pr
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host_gitlab.py
source-line: 272
parent: otaman-core.otaman_core.git_host_gitlab
---

## Docstring

Map a GitLab MR payload to our cross-provider PullRequest.

    GitLab uses iid (project-scoped) as the user-facing number. State
    vocabulary: opened/closed/merged; we normalise 'opened' → 'open'
    so callers can do a flat comparison with the GitHub adapter.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
