#!/usr/bin/env python3
"""Wiki ingestion spike — extract entities from a repo and emit .otaman/wiki/.

Usage:
    python scripts/wiki_ingest.py [src_dir] [--repo REPO] [--wiki WIKI_DIR] [--overwrite]

Defaults:
    src_dir   = src/  (relative to cwd)
    repo      = basename of cwd
    wiki_dir  = .otaman/wiki/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from otaman_core.wiki import ingest, HAS_TREE_SITTER


def main() -> int:
    if not HAS_TREE_SITTER:
        print("ERROR: tree-sitter and tree-sitter-python are required.", file=sys.stderr)
        print("  pip install tree-sitter tree-sitter-python", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("src_dir", nargs="?", default="src", help="Source directory to scan (default: src/)")
    parser.add_argument("--repo", default=None, help="Repo name used as entity-id prefix (default: cwd basename)")
    parser.add_argument("--wiki", default=".otaman/wiki", help="Output wiki directory (default: .otaman/wiki)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing entity files")
    args = parser.parse_args()

    cwd = Path.cwd()
    src_dir = Path(args.src_dir).resolve()
    wiki_dir = Path(args.wiki).resolve()
    repo_name = args.repo or cwd.name

    if not src_dir.is_dir():
        print(f"ERROR: src_dir not found: {src_dir}", file=sys.stderr)
        return 2

    print(f"Ingesting {src_dir} -> {wiki_dir}")
    print(f"  repo: {repo_name}  overwrite: {args.overwrite}")

    stats = ingest(
        src_dirs=[src_dir],
        wiki_dir=wiki_dir,
        repo_name=repo_name,
        src_root=src_dir.parent,
        overwrite=args.overwrite,
    )

    print(f"\nResults:")
    print(f"  files processed : {stats['files']}")
    print(f"  entities written: {stats['entities']}")
    print(f"  entities skipped: {stats['skipped']} (already exist)")
    print(f"  lines of code   : {stats['loc']}")
    print(f"  elapsed         : {stats['elapsed_sec']:.4f}s")
    print(f"  throughput      : {stats['loc_per_sec']:,.0f} LOC/sec")
    return 0


if __name__ == "__main__":
    sys.exit(main())
