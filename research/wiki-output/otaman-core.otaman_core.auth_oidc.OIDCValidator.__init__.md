---
id: otaman-core.otaman_core.auth_oidc.OIDCValidator.__init__
title: __init__
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/auth_oidc.py
source-line: 108
parent: otaman-core.otaman_core.auth_oidc.OIDCValidator
---

## Docstring

Args:
            config: required immutable per-service config
            cache_ttl: how long to trust the cached JWKS, seconds
            jwks_fetcher: optional ``f(url) -> dict`` for tests to bypass
                real HTTP fetch
            clock: optional ``f() -> float`` for tests to control time

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
