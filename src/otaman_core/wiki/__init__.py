"""otaman_core.wiki — static-analysis wiki ingestion pipeline (spike).

Extracts L3 (module/component) and L4 (code-unit) entities from Python source
via Tree-sitter and emits deterministic markdown entity files to .otaman/wiki/.

Public surface (spike):
    ingest()       — orchestrator; walk src dirs, emit entity files
    WikiEntity     — entity data type + markdown serializer
    HAS_TREE_SITTER — True when tree-sitter + tree-sitter-python are installed
"""

from ._entity import WikiEntity
from ._extract import HAS_TREE_SITTER
from .ingest import ingest

__all__ = ["ingest", "WikiEntity", "HAS_TREE_SITTER"]
