---
id: otaman-core.otaman_core.spawn.TerminalBackend
title: TerminalBackend
kind: component
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/spawn.py
source-line: 126
parent: otaman-core.otaman_core.spawn
---

## Docstring

Wraps a shell session in a recovery-friendly terminal multiplexer.

    Implementations: TmuxBackend (v0), WindowsTerminalBackend, ScreenBackend,
    HeadlessBackend (post-v0). Each implementation decides how to spawn the
    underlying process, name the session, and produce attach instructions.

    All methods are sync because the implementations shell out to short-lived
    OS commands; the runner daemon wraps them in a thread pool when serving
    HTTP requests.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
