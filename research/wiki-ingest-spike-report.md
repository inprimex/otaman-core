# Wiki Ingestion Pipeline — Spike Report (Task 1.7)

**Branch**: `agent/core-agent/wiki-ingestion-spike`  
**Date**: 2026-05-25  
**Author**: core-agent  
**Repo tested**: `otaman-core` (the smallest active Otaman repo)

---

## What was built

A Python module `src/otaman_core/wiki/` that:
1. Walks `.py` files in a source directory tree using `rglob`
2. Parses each file with **Tree-sitter** (Python grammar)
3. Extracts:
   - **L3 modules** (`kind: module`) — one per `.py` file
   - **L3 components** (`kind: component`) — Python classes
   - **L4 code units** (`kind: code-unit`) — functions and methods
4. Emits a deterministic markdown entity file per entity to `.otaman/wiki/`

Entity files follow the design.md Q2/Q7 format:
- YAML frontmatter with `id`, `title`, `kind`, `lens-tag`, `status`, `created-at`, `created-by`, `provenance`, `confidence`, `source-file`, `source-line`, optional `parent`
- `## Docstring` section (from source, if present)
- `## Synthesized description` section with `<!-- llm-managed:begin/end -->` fencing (empty — populated by task 1.8)
- `## Human notes` section with `<!-- human-edited -->` marker

---

## Performance results

| Metric | Value |
|--------|-------|
| Files processed | 16 |
| Entities written | 162 |
| Lines of code | 3,878 |
| Elapsed | 0.22 s |
| **Throughput** | **17,807 LOC/sec** |

Entity breakdown:

| Kind | Count |
|------|-------|
| module | 16 |
| component | 16 |
| code-unit | 130 |
| **Total** | **162** |

The 17,807 LOC/sec throughput is I/O + parse dominated. On this 3,878-LOC repo the full ingest takes 0.22 seconds end-to-end. Extrapolating: a 100,000-LOC codebase ingests in ~5.6 seconds; a 1M-LOC monorepo in ~56 seconds.

---

## Sample wiki output

See `research/wiki-output/`. Three representative samples:

**Module entity** (`otaman-core.otaman_core._resolve.md`):
```
kind: module
source-file: src/otaman_core/_resolve.py
source-line: 1
```
Docstring from module-level string; `## Synthesized description` left empty for LLM.

**Component entity** (`otaman-core.otaman_core.auth_oidc.OIDCValidator.md`):
```
kind: component
parent: otaman-core.otaman_core.auth_oidc
source-line: 105
```
Class-level docstring extracted; parent points to the module.

**Code-unit entity** (`otaman-core.otaman_core._resolve.find_maestro_root.md`):
```
kind: code-unit
parent: otaman-core.otaman_core._resolve
source-line: 83
```
Full multi-paragraph docstring preserved in body.

---

## Design compliance

| Design.md requirement | Status |
|-----------------------|--------|
| L3 components extracted | ✓ classes as `component`, modules as `module` |
| L4 code units extracted | ✓ functions + methods as `code-unit` |
| Deterministic frontmatter | ✓ YAML via PyYAML; stable field order |
| `lens-tag: c4` | ✓ default |
| `provenance: static-analysis` | ✓ |
| `confidence: 1.0` for static | ✓ |
| `<!-- llm-managed:begin/end -->` fencing | ✓ |
| `<!-- human-edited -->` section | ✓ |
| No-overwrite by default | ✓ existing files preserved |
| `parent` wikilink field | ✓ methods point to class, functions to module |
| Obsidian-compatible filenames | ✓ dotted IDs with no special chars |

---

## Findings and gaps

### 1. `.otaman` file vs directory conflict
In `otaman-core` (and likely all repos), `.otaman` is a **file** (the workspace marker), not a directory. The spec says `.otaman/wiki/` — this is a naming conflict. The spike outputs to `research/wiki-output/` as a workaround.

**Resolution options**:
- (a) Wiki lives at `<otaman-workspace-root>/.otaman/wiki/` (the meta repo), not per-source-repo — aligns with `.otaman` being the workspace pointer
- (b) Use `.otaman-wiki/` as a separate directory in source repos
- (c) Rename the marker file to `.otaman-marker` and free `.otaman/` for the wiki

Recommend **option (a)**: the spec references `<test-project>/.otaman/wiki/` but "test-project" likely means the **workspace**, not the source repo. The wiki is a workspace-level artifact, not a per-repo artifact. Needs design.md clarification.

### 2. Decorated functions missed at class level
The extractor correctly handles `decorated_definition` nodes at module level (e.g., `@property` methods), but nested decorated methods inside class bodies are already handled via `_unwrap_decorated`. Verified in tests.

### 3. `__init__.py` files emit module entities with title `__init__`
Cosmetic issue — the title should probably be the package name (e.g., `otaman_core` not `__init__`). Easy fix.

### 4. No cross-file wikilinks yet
Static extraction only produces `parent` links. Cross-file wikilinks (import relationships, call relationships) require Stack Graphs or LSP — deferred to the full implementation (post-spike).

### 5. Confidence model
Currently all statically-extracted facts get `confidence: 1.0`. The design.md confidence model (1.0 static, 0.5 LLM-synthesized, etc.) is wired into the frontmatter field but not yet used by any consumer.

---

## Recommendation for task 1.8

The `<!-- llm-managed:begin/end -->` fencing is in place. Task 1.8 (LLM ingest) can:
1. Read each entity file
2. Locate the fenced region
3. Feed the entity's docstring + source context to an LLM
4. Write synthesized prose into the fenced region

The `overwrite=False` default in `ingest()` ensures repeated static-analysis runs don't wipe LLM-written content. Task 1.8 needs its own write path that targets only the fenced region.

---

## Files delivered

- `src/otaman_core/wiki/__init__.py` — public surface
- `src/otaman_core/wiki/_entity.py` — WikiEntity dataclass + markdown serializer
- `src/otaman_core/wiki/_extract.py` — Tree-sitter extraction (L3 + L4)
- `src/otaman_core/wiki/ingest.py` — orchestrator
- `scripts/wiki_ingest.py` — CLI entry point
- `tests/test_wiki_ingest.py` — 26 tests, all passing
- `research/wiki-output/` — 162 entity files from `otaman-core`
- `research/wiki-ingest-spike-report.md` — this report
