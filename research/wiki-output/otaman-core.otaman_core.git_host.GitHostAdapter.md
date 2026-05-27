---
id: otaman-core.otaman_core.git_host.GitHostAdapter
title: GitHostAdapter
kind: component
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/git_host.py
source-line: 460
parent: otaman-core.otaman_core.git_host
---

## Docstring

Provider-agnostic PR read + comment write surface.

    Adapters live in ``git_host_<provider>.py`` (one per provider).
    All methods take the repo slug in canonical ``owner/repo`` form
    (Azure DevOps uses ``org/project/repo`` as a single string and
    the adapter normalises internally).

    Methods raise ``GitHostError`` on API failure — the caller decides
    whether to log-and-continue or bubble up.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
