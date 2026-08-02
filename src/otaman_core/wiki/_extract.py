"""Tree-sitter extraction — L3 components + L4 code units from Python source."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tree_sitter_python as _tspython
    from tree_sitter import Language as _Language
    from tree_sitter import Parser as _Parser

    _PY_LANGUAGE = _Language(_tspython.language())
    HAS_TREE_SITTER = True
except ImportError:  # pragma: no cover
    HAS_TREE_SITTER = False

from typing import TYPE_CHECKING

from ._entity import WikiEntity

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass
class FileParseResult:
    """Raw extraction result for one Python file — before cross-file resolution."""

    entities: list[WikiEntity]
    # Python module paths found in import statements, e.g. ["otaman_core._resolve", "yaml"]
    imported_modules: list[str] = field(default_factory=list)
    # class entity-id → list of raw base-class names, e.g. {"repo.mod.Foo": ["Bar", "Enum"]}
    class_bases: dict[str, list[str]] = field(default_factory=dict)
    loc: int = 0


def module_dotted_id(repo_name: str, rel_path: Path) -> str:
    """Convert a repo-relative file path to a dotted module entity-id.

    src/otaman_core/_resolve.py  ->  otaman-core.otaman_core._resolve
    """
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    return repo_name + "." + ".".join(parts)


def _identifier(node, src: bytes) -> str | None:
    for child in node.children:
        if child.type == "identifier":
            return src[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return None


def _dotted_name(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _docstring(node, src: bytes) -> str | None:
    for child in node.children:
        if child.type == "block":
            for c in child.children:
                if c.type == "expression_statement":
                    for cc in c.children:
                        if cc.type == "string":
                            raw = src[cc.start_byte : cc.end_byte].decode("utf-8", errors="replace")
                            return _strip_quotes(raw)
    return None


def _module_docstring(root, src: bytes) -> str | None:
    for child in root.children:
        if child.type == "expression_statement":
            for cc in child.children:
                if cc.type == "string":
                    raw = src[cc.start_byte : cc.end_byte].decode("utf-8", errors="replace")
                    return _strip_quotes(raw)
        break
    return None


def _strip_quotes(s: str) -> str:
    for q in ('"""', "'''", '"', "'"):
        if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
            return s[len(q) : len(s) - len(q)].strip()
    return s.strip()


def _extract_imports(root, src: bytes) -> list[str]:
    """Return Python module paths from top-level import statements."""
    modules: list[str] = []
    for node in root.children:
        if node.type == "import_statement":
            # import yaml  /  import os.path
            for c in node.children:
                if c.type in ("dotted_name", "aliased_import"):
                    inner = c if c.type == "dotted_name" else next(
                        (x for x in c.children if x.type == "dotted_name"), None
                    )
                    if inner:
                        modules.append(_dotted_name(inner, src))
        elif node.type == "import_from_statement":
            # from otaman_core._resolve import X  /  from . import Y (skip relative)
            for c in node.children:
                if c.type == "dotted_name":
                    modules.append(_dotted_name(c, src))
                    break
                if c.type == "relative_import":
                    break  # skip relative imports — can't resolve them statically
    return modules


def _extract_bases(class_node, src: bytes) -> list[str]:
    """Return raw base-class names from a class definition."""
    bases: list[str] = []
    for child in class_node.children:
        if child.type == "argument_list":
            for c in child.children:
                if c.type == "identifier":
                    bases.append(src[c.start_byte : c.end_byte].decode("utf-8", errors="replace"))
                elif c.type == "dotted_name":
                    # e.g. class Foo(some.Base)
                    bases.append(_dotted_name(c, src).rsplit(".", 1)[-1])
    return bases


def parse_file(src_bytes: bytes, rel_path: Path, repo_name: str) -> FileParseResult:
    """Full parse of one Python file — entities + imports + base classes + LOC.

    This is the primary entry point for the two-pass ingest. Use
    ``extract_entities`` when you only need the entity list.
    """
    if not HAS_TREE_SITTER:
        raise ImportError("tree-sitter and tree-sitter-python are required for wiki ingestion")

    parser = _Parser(_PY_LANGUAGE)
    tree = parser.parse(src_bytes)
    root = tree.root_node

    mod_id = module_dotted_id(repo_name, rel_path)
    src_str = str(rel_path)
    entities: list[WikiEntity] = []
    class_bases: dict[str, list[str]] = {}

    # L3 module entity
    entities.append(
        WikiEntity(
            id=mod_id,
            title=mod_id.rsplit(".", 1)[-1],
            kind="module",
            source_file=src_str,
            source_line=1,
            docstring=_module_docstring(root, src_bytes),
        )
    )

    for node in root.children:
        if node.type == "class_definition":
            cls_name = _identifier(node, src_bytes)
            if not cls_name:
                continue
            cls_id = f"{mod_id}.{cls_name}"
            bases = _extract_bases(node, src_bytes)
            if bases:
                class_bases[cls_id] = bases
            entities.append(
                WikiEntity(
                    id=cls_id,
                    title=cls_name,
                    kind="component",
                    source_file=src_str,
                    source_line=node.start_point[0] + 1,
                    docstring=_docstring(node, src_bytes),
                    parent_id=mod_id,
                )
            )
            for child in node.children:
                if child.type == "block":
                    for c in child.children:
                        if c.type in ("function_definition", "decorated_definition"):
                            fn_node = c if c.type == "function_definition" else _unwrap_decorated(c)
                            if fn_node is None:
                                continue
                            m_name = _identifier(fn_node, src_bytes)
                            if not m_name:
                                continue
                            entities.append(
                                WikiEntity(
                                    id=f"{cls_id}.{m_name}",
                                    title=m_name,
                                    kind="code-unit",
                                    source_file=src_str,
                                    source_line=fn_node.start_point[0] + 1,
                                    docstring=_docstring(fn_node, src_bytes),
                                    parent_id=cls_id,
                                )
                            )

        elif node.type in ("function_definition", "decorated_definition"):
            fn_node = node if node.type == "function_definition" else _unwrap_decorated(node)
            if fn_node is None:
                continue
            fn_name = _identifier(fn_node, src_bytes)
            if not fn_name:
                continue
            entities.append(
                WikiEntity(
                    id=f"{mod_id}.{fn_name}",
                    title=fn_name,
                    kind="code-unit",
                    source_file=src_str,
                    source_line=fn_node.start_point[0] + 1,
                    docstring=_docstring(fn_node, src_bytes),
                    parent_id=mod_id,
                )
            )

    return FileParseResult(
        entities=entities,
        imported_modules=_extract_imports(root, src_bytes),
        class_bases=class_bases,
        loc=src_bytes.count(b"\n"),
    )


def extract_entities(src_bytes: bytes, rel_path: Path, repo_name: str) -> list[WikiEntity]:
    """Convenience wrapper — returns only the entity list (no relation resolution)."""
    return parse_file(src_bytes, rel_path, repo_name).entities


def _unwrap_decorated(node: "Node") -> "Node | None":
    for child in node.children:
        if child.type == "function_definition":
            return child
    return None
