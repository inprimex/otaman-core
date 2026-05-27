---
id: otaman-core.otaman_core.auth_oidc
title: auth_oidc
kind: module
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/auth_oidc.py
source-line: 1
---

## Docstring

OIDC token validation — Zitadel-anchored auth boundary.

Per ADR-010, otaman-bridge / otaman-runner / otaman-web validate JWTs
issued by a configured OIDC provider (default: Zitadel) at the network
boundary. This module is the shared validator used by all three services.

Per the Zitadel integration spec (otaman-meta/strategy/zitadel-integration.md
§4), the validator:
- Fetches and caches the JWKS document for 5 minutes
- Verifies token signature against the matching key by ``kid``
- Verifies ``iss``, ``aud``, ``exp`` per RFC 7519 + RFC 8725
- Extracts roles from Zitadel's project-role claim
  ``urn:zitadel:iam:org:project:roles`` (a dict keyed by role)
- Optionally enforces a ``required_role`` per validator instance

The validator never raises on token validity errors — it always returns
``OIDCAuthResult(ok=False, error=...)`` so the daemon can turn that into
a 401 response. ``OIDCError`` is reserved for configuration / unrecoverable
JWKS-fetch problems.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
