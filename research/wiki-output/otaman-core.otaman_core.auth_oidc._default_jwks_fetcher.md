---
id: otaman-core.otaman_core.auth_oidc._default_jwks_fetcher
title: _default_jwks_fetcher
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/auth_oidc.py
source-line: 286
parent: otaman-core.otaman_core.auth_oidc
---

## Docstring

Fetch a JWKS document via HTTPS.

    Sets an explicit User-Agent — Cloudflare's Bot Fight Mode (and similar
    TLS-terminating proxies) block urllib's default ``Python-urllib/3.x``
    with 403. Any deployment fronting the IdP with a CDN/WAF hits this
    on every token validation. Caught 2026-05-19 during otaman-bridge's
    Cloudflare Tunnel migration.

    Raises OIDCError on any network / parse failure.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
