---
id: otaman-core.otaman_core.auth_oidc.OIDCValidator.validate
title: validate
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/auth_oidc.py
source-line: 133
parent: otaman-core.otaman_core.auth_oidc.OIDCValidator
---

## Docstring

Parse + verify a ``Authorization: Bearer <token>`` header.

        Returns an ``OIDCAuthResult`` — never raises for token-validity
        problems. Raises ``OIDCError`` only for unrecoverable JWKS-fetch
        failure (which the caller should turn into a 5xx, not a 401).

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
