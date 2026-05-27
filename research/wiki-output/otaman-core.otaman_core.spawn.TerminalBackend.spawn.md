---
id: otaman-core.otaman_core.spawn.TerminalBackend.spawn
title: spawn
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/spawn.py
source-line: 140
parent: otaman-core.otaman_core.spawn.TerminalBackend
---

## Docstring

Start ``command`` in a wrapped session and return attach info.

        The runner has already resolved the worktree, computed env vars
        (CLAUDE_CONFIG_DIR, OTAMAN_ROOT, OTAMAN_ACTIVE_ROUTING, etc.),
        and decided cwd. The backend's only job is to wrap the command
        in its terminal multiplexer and return how to attach.

        Raises:
            BackendError: on multiplexer failure (e.g. tmux missing,
                session-name collision).

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
