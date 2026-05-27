---
id: otaman-core.otaman_core._resolve.expand_config_dir
title: expand_config_dir
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_resolve.py
source-line: 321
parent: otaman-core.otaman_core._resolve
---

## Docstring

Expand a ``config_dir`` spec for a target shell.

    The value comes from ``launch-settings.yaml accounts.<name>.config_dir``
    (e.g. ``~/.claude-personal``). Different shells expand tildes and env
    variables differently, and some shells (wsl, ssh) resolve on a different
    host entirely — in those cases we defer expansion to the target shell.

    Args:
        config_dir: Raw value from YAML. May contain ``~``, ``$HOME``,
            ``${HOME}``, ``$USERPROFILE``, ``${USERPROFILE}``.
        shell: Target shell name. Understood values:
            - ``powershell`` / ``pwsh`` / ``cmd`` — native Windows path output
            - ``bash`` / ``zsh`` / ``fish`` — POSIX-slash output, expanded
            - ``wsl`` / ``ssh`` — pass-through; target shell resolves
        home: Override for ``$HOME`` / ``~``. Defaults to ``Path.home()``.
            Mainly for tests and cross-shell expansion (e.g. a Windows
            launcher computing a WSL path without needing to shell out).

    Returns:
        A path string appropriate for the target shell. Empty input returns
        an empty string.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
