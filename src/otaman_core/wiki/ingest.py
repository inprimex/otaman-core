"""Wiki ingestion orchestrator — walk Python source, emit entity files."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

from ._extract import extract_entities
from ._entity import WikiEntity


def ingest(
    src_dirs: Iterable[Path],
    wiki_dir: Path,
    repo_name: str,
    *,
    src_root: Path | None = None,
    overwrite: bool = False,
) -> dict:
    """Walk Python files in *src_dirs*, extract entities, write to *wiki_dir*.

    Args:
        src_dirs: directories to recurse into.
        wiki_dir: output directory (created if absent).
        repo_name: short repo identifier used as entity-id prefix (e.g. ``"otaman-core"``).
        src_root: root used to compute relative paths for entity ids; if None, each
            src_dir is treated as the root.
        overwrite: if False (default), skip files that already exist so human edits
            and LLM-managed regions are preserved.

    Returns:
        Stats dict with keys ``files``, ``entities``, ``loc``, ``elapsed_sec``,
        ``loc_per_sec``, ``skipped``.
    """
    wiki_dir.mkdir(parents=True, exist_ok=True)

    files_processed = 0
    total_entities = 0
    total_loc = 0
    skipped = 0
    t0 = time.perf_counter()

    for src_dir in src_dirs:
        root = src_root if src_root is not None else src_dir.parent
        for py_file in sorted(src_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue

            src_bytes = py_file.read_bytes()
            total_loc += src_bytes.count(b"\n")

            rel = py_file.relative_to(root)
            entities = extract_entities(src_bytes, rel, repo_name)
            files_processed += 1

            for entity in entities:
                out_path = wiki_dir / entity.filename()
                if out_path.exists() and not overwrite:
                    skipped += 1
                    continue
                out_path.write_text(entity.to_markdown(), encoding="utf-8")
                total_entities += 1

    elapsed = time.perf_counter() - t0
    return {
        "files": files_processed,
        "entities": total_entities,
        "skipped": skipped,
        "loc": total_loc,
        "elapsed_sec": round(elapsed, 4),
        "loc_per_sec": round(total_loc / elapsed, 1) if elapsed > 0 else 0.0,
    }
