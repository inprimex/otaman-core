---
id: otaman-core.otaman_core._secrets
title: _secrets
kind: module
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_secrets.py
source-line: 1
---

## Docstring

Secret resolution chain for otaman.

Resolves secret references declared in launch-settings.yaml (and later
platform.yaml) through a tiered source chain:

    1. Process env  — variable already set in the shell
    2. dotenv       — .otaman/secrets.env (gitignored, mode 0600)
    3. keyring      — OS keychain via the keyring package (optional dep)
    4. (post-v1)    — vault / aws-sm / gcp-sm / azure-kv

YAML shape accepted (backwards-compatible short form first):

    # Short form
    bot_token_env: OTAMAN_TG_BOT_PERSONAL

    # Long form
    bot_token:
      sources:
        - { type: env,     name: OTAMAN_TG_BOT_PERSONAL }
        - { type: dotenv,  name: OTAMAN_TG_BOT_PERSONAL }
        - { type: keyring, service: otaman, account: tg-personal }

Usage:
    from _secrets import SecretRef, resolve, resolve_or_fail

    ref = SecretRef.from_config(config_value_from_yaml)
    value = resolve(ref, maestro_root=Path("/path/to/workspace"))  # legacy: maestro_root param renamed at 1.0

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
