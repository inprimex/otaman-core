"""Wiki ingestion orchestrator — walk Python source, emit entity files with graph links."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

from ._extract import parse_file
from ._entity import WikiEntity


def ingest(
    src_dirs: Iterable[Path],
    wiki_dir: Path,
    repo_name: str,
    *,
    src_root: Path | None = None,
    overwrite: bool = False,
) -> dict:
    """Two-pass ingest: collect all entities, resolve graph relations, write files.

    Pass 1 — parse every .py file: extract entities, import statements, base class names.
    Pass 2 — resolve relations (parent→child, child→parent, module→imports, class→bases)
             and write markdown entity files with [[wikilinks]] in the body.

    Args:
        src_dirs: directories to recurse into.
        wiki_dir: output directory (created if absent).
        repo_name: short repo identifier used as entity-id prefix (e.g. ``"otaman-core"``).
        src_root: root used to compute relative paths for entity ids.
        overwrite: if False (default), skip files that already exist so human edits
            and LLM-managed regions are preserved.

    Returns:
        Stats dict: ``files``, ``entities``, ``skipped``, ``loc``, ``elapsed_sec``,
        ``loc_per_sec``, ``links``.
    """
    t0 = time.perf_counter()
    wiki_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Pass 1: parse all files, accumulate raw data
    # -----------------------------------------------------------------------
    all_entities: dict[str, WikiEntity] = {}          # id -> entity
    module_python_to_id: dict[str, str] = {}          # "otaman_core._resolve" -> entity_id
    all_imports: dict[str, list[str]] = {}             # module_entity_id -> [python_module_paths]
    all_bases: dict[str, list[str]] = {}               # class_entity_id -> [base_names]
    total_loc = 0
    files_processed = 0

    for src_dir in src_dirs:
        root = src_root if src_root is not None else src_dir.parent
        for py_file in sorted(src_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            src_bytes = py_file.read_bytes()
            rel = py_file.relative_to(root)
            result = parse_file(src_bytes, rel, repo_name)
            total_loc += result.loc
            files_processed += 1

            for entity in result.entities:
                all_entities[entity.id] = entity

            # Module entity is always first in result.entities
            mod_entity = result.entities[0]
            # Python dotted path without repo prefix: "otaman_core._resolve"
            py_path = ".".join(mod_entity.id.split(".")[1:])
            module_python_to_id[py_path] = mod_entity.id

            if result.imported_modules:
                all_imports[mod_entity.id] = result.imported_modules
            all_bases.update(result.class_bases)

    # -----------------------------------------------------------------------
    # Pass 2: build relations, write files
    # -----------------------------------------------------------------------
    # children map: parent_id -> [child_id, ...]
    children: dict[str, list[str]] = {}
    for entity in all_entities.values():
        if entity.parent_id:
            children.setdefault(entity.parent_id, []).append(entity.id)

    # title registry for display names
    def _display(entity_id: str) -> str:
        e = all_entities.get(entity_id)
        return e.title if e else entity_id.rsplit(".", 1)[-1]

    # name→id lookup for base-class resolution (title -> entity_id, last-wins)
    title_to_ids: dict[str, list[str]] = {}
    for eid, e in all_entities.items():
        title_to_ids.setdefault(e.title, []).append(eid)

    total_links = 0
    written = 0
    skipped = 0

    for entity_id, entity in all_entities.items():
        relations = []

        # child → parent (upward link)
        if entity.parent_id and entity.parent_id in all_entities:
            label = "Part of"
            relations.append((label, entity.parent_id, _display(entity.parent_id)))

        # parent → children (downward links, split by kind)
        child_ids = sorted(children.get(entity_id, []))
        if child_ids:
            components = [(cid, _display(cid)) for cid in child_ids
                         if all_entities.get(cid) and all_entities[cid].kind == "component"]
            funcs = [(cid, _display(cid)) for cid in child_ids
                     if all_entities.get(cid) and all_entities[cid].kind != "component"]
            for cid, cdisplay in components:
                relations.append(("Contains", cid, cdisplay))
            for cid, cdisplay in funcs:
                relations.append(("Contains", cid, cdisplay))

        # module → imports (internal only — skip stdlib/third-party)
        for raw_mod in all_imports.get(entity_id, []):
            target_id = module_python_to_id.get(raw_mod)
            if target_id and target_id != entity_id and target_id in all_entities:
                relations.append(("Imports", target_id, _display(target_id)))

        # class → base classes
        for base_name in all_bases.get(entity_id, []):
            # Try same module first, then global
            parent_mod = entity.parent_id or ""
            candidate = f"{parent_mod}.{base_name}"
            if candidate in all_entities:
                relations.append(("Inherits", candidate, base_name))
            else:
                matches = title_to_ids.get(base_name, [])
                if len(matches) == 1:
                    relations.append(("Inherits", matches[0], base_name))
                # Multiple matches → ambiguous; skip to avoid wrong links

        entity.relations = relations
        total_links += len(relations)

        out_path = wiki_dir / entity.filename()
        if out_path.exists() and not overwrite:
            skipped += 1
            continue
        out_path.write_text(entity.to_markdown(), encoding="utf-8")
        written += 1

    elapsed = time.perf_counter() - t0
    return {
        "files": files_processed,
        "entities": written,
        "skipped": skipped,
        "loc": total_loc,
        "links": total_links,
        "elapsed_sec": round(elapsed, 4),
        "loc_per_sec": round(total_loc / elapsed, 1) if elapsed > 0 else 0.0,
    }
