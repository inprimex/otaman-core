---
id: otaman-core.otaman_core.git_host_azure.AzureDevOpsAdapter.list_comments
title: list_comments
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host_azure.py
source-line: 305
parent: otaman-core.otaman_core.git_host_azure.AzureDevOpsAdapter
---

## Docstring

Flatten every comment from every thread on the PR.

        Azure groups comments into threads; a plain list view is what
        the Protocol exposes, so we flatten. The thread id rides along
        in ``Comment.raw['_thread_id']`` for callers who need it.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
