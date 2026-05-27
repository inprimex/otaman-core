---
id: otaman-core.otaman_core.auth_oidc._extract_roles
title: _extract_roles
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/auth_oidc.py
source-line: 248
parent: otaman-core.otaman_core.auth_oidc
---

## Docstring

Extract role keys from Zitadel's project-role claim shape.

    Zitadel emits roles under a project-scoped claim that includes the
    project resource id:

        "urn:zitadel:iam:org:project:<PROJECT_ID>:roles": {
            "otaman:developer": {"<org-id>": "<org-domain>"},
            "otaman:viewer":    {"<org-id>": "<org-domain>"},
        }

    Discovered empirically against Zitadel v2.66 (2026-05-15 smoke test
    on Greenbin pilot dogfood). Older Zitadel versions also recognised
    the legacy claim name without the project id; we accept both for
    forward / backward compatibility.

    Multiple project claims may appear in one token (one per project the
    subject has roles in). Returns the union of role keys across all of
    them. Returns ``[]`` if no role claim is present or every claim is
    malformed.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
