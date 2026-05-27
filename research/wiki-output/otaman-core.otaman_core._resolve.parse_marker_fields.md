---
id: otaman-core.otaman_core._resolve.parse_marker_fields
title: parse_marker_fields
kind: code-unit
lens-tag: c4
status: draft
created-at: '2026-05-24T22:44:44Z'
created-by: otaman-core/wiki-ingest
provenance: static-analysis
confidence: 1.0
source-file: src/otaman_core/_resolve.py
source-line: 251
parent: otaman-core.otaman_core._resolve
---

## Docstring

Parse a marker file into a dict of fields.

    Accepts two formats, chosen line-by-line:

    - **Legacy** — a single bare line holding the relative path to the
      otaman folder (e.g. ``../my-otaman``). Becomes ``maestro_root``
      (legacy: field renamed to ``otaman_root`` at 1.0).
    - **Extended** — ``key: value`` lines for known fields, plus an
      optional bare path line. Current known fields: ``otaman_root``,
      ``maestro_root`` (legacy: renamed at 1.0), ``expected_account``.

    Unknown ``key: value`` lines are ignored so that Windows absolute
    paths containing a colon (``C:/foo``) continue to parse as bare
    ``otaman_root`` / ``maestro_root`` values. Comment (``#``) and blank
    lines are skipped.

## Synthesized description
<!-- llm-managed:begin -->

<!-- llm-managed:end -->

## Human notes
<!-- human-edited -->
