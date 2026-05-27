---
id: otaman-core.otaman_core.spawn.SpawnMode
title: SpawnMode
kind: component
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/spawn.py
source-line: 26
parent: otaman-core.otaman_core.spawn
---

## Docstring

How the spawned session is wrapped for the user.

    INTERACTIVE: human attends in a tmux/Windows-Terminal session;
        AttachInfo is returned so the caller can exec the attach command.
    HEADLESS: no user attends; harness writes transcript to disk/NATS;
        the runner publishes lifecycle events.
    HYBRID: interactive AND transcript-publishing — spectator-friendly.

    Only INTERACTIVE is implemented in v0. HEADLESS / HYBRID stubs
    raise ``NotImplementedError`` so the wiring is forced through one
    code path until they ship.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
