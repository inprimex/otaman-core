"""Tests for the wiki ingestion pipeline (task 1.7 spike)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from otaman_core.wiki import HAS_TREE_SITTER, WikiEntity, ingest
from otaman_core.wiki._extract import extract_entities, module_dotted_id, parse_file
from otaman_core.wiki._entity import WikiEntity as _WikiEntity, Relation


pytestmark = pytest.mark.skipif(
    not HAS_TREE_SITTER,
    reason="tree-sitter and tree-sitter-python are required",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_SRC = b'''\
"""Sample module docstring."""

class Greeter:
    """Greets things."""

    def __init__(self, name: str) -> None:
        """Store name."""
        self.name = name

    def greet(self) -> str:
        """Return greeting."""
        return f"Hello, {self.name}"


def helper(x: int) -> int:
    """A helper function."""
    return x * 2
'''


# ---------------------------------------------------------------------------
# module_dotted_id
# ---------------------------------------------------------------------------

class TestModuleDottedId:
    def test_strips_src_prefix(self):
        rel = Path("src/otaman_core/_resolve.py")
        assert module_dotted_id("otaman-core", rel) == "otaman-core.otaman_core._resolve"

    def test_no_src_prefix(self):
        rel = Path("otaman_core/utils.py")
        assert module_dotted_id("myrepo", rel) == "myrepo.otaman_core.utils"

    def test_nested(self):
        rel = Path("src/pkg/sub/mod.py")
        assert module_dotted_id("r", rel) == "r.pkg.sub.mod"


# ---------------------------------------------------------------------------
# extract_entities
# ---------------------------------------------------------------------------

class TestExtractEntities:
    @pytest.fixture
    def entities(self):
        return extract_entities(_SAMPLE_SRC, Path("src/mymod.py"), "myrepo")

    def test_returns_list(self, entities):
        assert isinstance(entities, list)
        assert len(entities) > 0

    def test_module_entity_present(self, entities):
        mod = next((e for e in entities if e.kind == "module"), None)
        assert mod is not None
        assert mod.id == "myrepo.mymod"
        assert mod.docstring is not None
        assert "Sample module" in mod.docstring

    def test_class_entity_present(self, entities):
        cls = next((e for e in entities if e.kind == "component" and e.title == "Greeter"), None)
        assert cls is not None
        assert cls.id == "myrepo.mymod.Greeter"
        assert "Greets things" in cls.docstring

    def test_methods_extracted(self, entities):
        init_e = next((e for e in entities if e.title == "__init__"), None)
        assert init_e is not None
        assert init_e.kind == "code-unit"
        assert init_e.parent_id == "myrepo.mymod.Greeter"

        greet_e = next((e for e in entities if e.title == "greet"), None)
        assert greet_e is not None
        assert greet_e.kind == "code-unit"

    def test_top_level_function(self, entities):
        fn = next((e for e in entities if e.title == "helper"), None)
        assert fn is not None
        assert fn.kind == "code-unit"
        assert fn.parent_id == "myrepo.mymod"
        assert "helper function" in fn.docstring

    def test_source_line_is_positive(self, entities):
        for e in entities:
            assert e.source_line >= 1, f"{e.id} has bad source_line"

    def test_entity_ids_unique(self, entities):
        ids = [e.id for e in entities]
        assert len(ids) == len(set(ids)), "duplicate entity ids"


# ---------------------------------------------------------------------------
# WikiEntity.to_markdown
# ---------------------------------------------------------------------------

class TestWikiEntityMarkdown:
    def _entity(self, **kwargs):
        defaults = dict(
            id="repo.mod.Foo",
            title="Foo",
            kind="component",
            source_file="src/mod.py",
            source_line=10,
        )
        defaults.update(kwargs)
        return WikiEntity(**defaults)

    def test_has_frontmatter_delimiters(self):
        md = self._entity().to_markdown()
        assert md.startswith("---\n")
        assert "\n---\n" in md

    def test_frontmatter_has_required_fields(self):
        md = self._entity().to_markdown()
        fm_block = md.split("---\n")[1]
        for field in ("id:", "title:", "kind:", "lens-tag:", "status:", "created-at:",
                      "created-by:", "provenance:", "confidence:", "source-file:", "source-line:"):
            assert field in fm_block, f"missing {field!r}"

    def test_llm_managed_fencing_present(self):
        md = self._entity().to_markdown()
        assert "<!-- llm-managed:begin -->" in md
        assert "<!-- llm-managed:end -->" in md

    def test_human_edited_section_present(self):
        md = self._entity().to_markdown()
        assert "<!-- human-edited -->" in md

    def test_docstring_in_body(self):
        md = self._entity(docstring="This is a docstring.").to_markdown()
        assert "This is a docstring." in md
        assert "## Docstring" in md

    def test_no_docstring_section_when_absent(self):
        md = self._entity(docstring=None).to_markdown()
        assert "## Docstring" not in md

    def test_parent_in_frontmatter_when_set(self):
        md = self._entity(parent_id="repo.mod").to_markdown()
        assert "parent: repo.mod" in md

    def test_special_chars_in_docstring_safe(self):
        # Colons + quotes in docstring must not break YAML frontmatter
        e = self._entity(docstring='Has: "colons" and {braces}.')
        md = e.to_markdown()
        assert "Has: " in md  # in body, not frontmatter — should not raise

    def test_filename_equals_id_plus_md(self):
        e = self._entity(id="repo.mod.Cls.method")
        assert e.filename() == "repo.mod.Cls.method.md"


# ---------------------------------------------------------------------------
# ingest() orchestrator
# ---------------------------------------------------------------------------

class TestIngest:
    def _write_py(self, tmp_path: Path, name: str, content: bytes) -> Path:
        src_dir = tmp_path / "src" / "mypkg"
        src_dir.mkdir(parents=True, exist_ok=True)
        f = src_dir / name
        f.write_bytes(content)
        return src_dir

    def test_creates_wiki_dir(self, tmp_path):
        src_dir = self._write_py(tmp_path, "a.py", b"def f(): pass\n")
        wiki_dir = tmp_path / "wiki"
        assert not wiki_dir.exists()
        ingest([src_dir], wiki_dir, "myrepo", src_root=tmp_path / "src")
        assert wiki_dir.is_dir()

    def test_entity_files_written(self, tmp_path):
        src_dir = self._write_py(tmp_path, "b.py", b"class X: pass\n")
        wiki_dir = tmp_path / "wiki"
        stats = ingest([src_dir], wiki_dir, "myrepo", src_root=tmp_path / "src")
        assert stats["entities"] > 0
        files = list(wiki_dir.glob("*.md"))
        assert len(files) == stats["entities"]

    def test_stats_keys(self, tmp_path):
        src_dir = self._write_py(tmp_path, "c.py", b"x = 1\n")
        stats = ingest([src_dir], tmp_path / "wiki", "r", src_root=tmp_path / "src")
        for key in ("files", "entities", "skipped", "loc", "elapsed_sec", "loc_per_sec", "links"):
            assert key in stats

    def test_no_overwrite_by_default(self, tmp_path):
        src_dir = self._write_py(tmp_path, "d.py", b"def g(): pass\n")
        wiki_dir = tmp_path / "wiki"
        stats1 = ingest([src_dir], wiki_dir, "r", src_root=tmp_path / "src")
        # Second run — same files, should skip
        stats2 = ingest([src_dir], wiki_dir, "r", src_root=tmp_path / "src")
        assert stats2["entities"] == 0
        assert stats2["skipped"] == stats1["entities"]

    def test_overwrite_replaces_existing(self, tmp_path):
        src_dir = self._write_py(tmp_path, "e.py", b"def h(): pass\n")
        wiki_dir = tmp_path / "wiki"
        ingest([src_dir], wiki_dir, "r", src_root=tmp_path / "src")
        stats2 = ingest([src_dir], wiki_dir, "r", src_root=tmp_path / "src", overwrite=True)
        assert stats2["entities"] > 0
        assert stats2["skipped"] == 0

    def test_skips_pycache(self, tmp_path):
        src_dir = self._write_py(tmp_path, "f.py", b"x = 1\n")
        pycache = src_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "cached.py").write_bytes(b"# cached\n")
        wiki_dir = tmp_path / "wiki"
        stats = ingest([src_dir], wiki_dir, "r", src_root=tmp_path / "src")
        cached_files = [f for f in wiki_dir.glob("*__pycache__*")]
        assert cached_files == []

    def test_loc_per_sec_positive(self, tmp_path):
        src_dir = self._write_py(tmp_path, "g.py", b"x = 1\ny = 2\n")
        stats = ingest([src_dir], tmp_path / "wiki", "r", src_root=tmp_path / "src")
        assert stats["loc_per_sec"] > 0


# ---------------------------------------------------------------------------
# parse_file — two-pass raw extraction
# ---------------------------------------------------------------------------

_SRC_WITH_IMPORTS = b'''\
"""A module that imports things."""

import os
import yaml
from pathlib import Path
from otaman_core._resolve import find_maestro_root

class Foo:
    pass
'''

_SRC_WITH_BASES = b'''\
"""A module with inheritance."""

class Base:
    pass

class Child(Base):
    pass

class Multi(Base, dict):
    pass
'''


class TestParseFile:
    def test_returns_entities(self):
        result = parse_file(_SAMPLE_SRC, Path("src/mymod.py"), "myrepo")
        assert len(result.entities) > 0

    def test_loc_count(self):
        result = parse_file(_SAMPLE_SRC, Path("src/mymod.py"), "myrepo")
        assert result.loc == _SAMPLE_SRC.count(b"\n")

    def test_imported_modules_captured(self):
        result = parse_file(_SRC_WITH_IMPORTS, Path("src/mymod.py"), "myrepo")
        # Should capture top-level import targets
        assert "os" in result.imported_modules
        assert "yaml" in result.imported_modules
        # from-import: module path "pathlib"
        assert "pathlib" in result.imported_modules
        # from-import: module path "otaman_core._resolve"
        assert "otaman_core._resolve" in result.imported_modules

    def test_relative_imports_skipped(self):
        src = b"from . import sibling\nfrom .utils import helper\n"
        result = parse_file(src, Path("src/mymod.py"), "myrepo")
        # Relative imports must not appear — they can't be resolved statically
        for mod in result.imported_modules:
            assert not mod.startswith(".")

    def test_class_bases_captured(self):
        result = parse_file(_SRC_WITH_BASES, Path("src/mymod.py"), "myrepo")
        # Child(Base) — should record bases
        child_id = "myrepo.mymod.Child"
        assert child_id in result.class_bases
        assert "Base" in result.class_bases[child_id]

    def test_class_with_no_bases_absent(self):
        result = parse_file(_SRC_WITH_BASES, Path("src/mymod.py"), "myrepo")
        base_id = "myrepo.mymod.Base"
        assert base_id not in result.class_bases

    def test_multiple_bases(self):
        result = parse_file(_SRC_WITH_BASES, Path("src/mymod.py"), "myrepo")
        multi_id = "myrepo.mymod.Multi"
        assert multi_id in result.class_bases
        assert "Base" in result.class_bases[multi_id]
        assert "dict" in result.class_bases[multi_id]


# ---------------------------------------------------------------------------
# WikiEntity.to_markdown with relations
# ---------------------------------------------------------------------------

class TestMarkdownWithRelations:
    def _entity(self, **kwargs) -> WikiEntity:
        defaults = dict(
            id="repo.mod.Foo",
            title="Foo",
            kind="component",
            source_file="src/mod.py",
            source_line=10,
        )
        defaults.update(kwargs)
        return WikiEntity(**defaults)

    def test_no_relations_block_when_empty(self):
        md = self._entity().to_markdown()
        assert "**Part of**" not in md
        assert "**Contains**" not in md
        assert "[[" not in md

    def test_single_relation_rendered(self):
        e = self._entity()
        e.relations = [("Part of", "repo.mod", "mod")]
        md = e.to_markdown()
        assert "**Part of**" in md
        assert "[[repo.mod|mod]]" in md

    def test_multiple_same_label_on_one_line(self):
        e = self._entity()
        e.relations = [
            ("Contains", "repo.mod.bar", "bar"),
            ("Contains", "repo.mod.baz", "baz"),
        ]
        md = e.to_markdown()
        assert "**Contains**" in md
        # Both links should appear
        assert "[[repo.mod.bar|bar]]" in md
        assert "[[repo.mod.baz|baz]]" in md

    def test_relations_before_docstring(self):
        e = self._entity(docstring="My docstring.")
        e.relations = [("Part of", "repo.mod", "mod")]
        md = e.to_markdown()
        rel_pos = md.index("**Part of**")
        doc_pos = md.index("## Docstring")
        assert rel_pos < doc_pos

    def test_relations_before_llm_block(self):
        e = self._entity()
        e.relations = [("Inherits", "repo.mod.Base", "Base")]
        md = e.to_markdown()
        rel_pos = md.index("**Inherits**")
        llm_pos = md.index("<!-- llm-managed:begin -->")
        assert rel_pos < llm_pos


# ---------------------------------------------------------------------------
# ingest() — two-pass graph link integration
# ---------------------------------------------------------------------------

_PARENT_SRC = b'''\
"""Parent module."""

class Alpha:
    """Alpha class."""
    pass
'''

_CHILD_SRC = b'''\
"""Child module that imports parent."""

from mypkg.parent import Alpha

class Beta(Alpha):
    """Beta inherits Alpha (cross-module)."""
    pass
'''


class TestIngestGraphLinks:
    def _setup_two_files(self, tmp_path: Path):
        src_dir = tmp_path / "src" / "mypkg"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "parent.py").write_bytes(_PARENT_SRC)
        (src_dir / "child.py").write_bytes(_CHILD_SRC)
        return src_dir

    def test_links_key_in_stats(self, tmp_path):
        src_dir = self._setup_two_files(tmp_path)
        stats = ingest([src_dir], tmp_path / "wiki", "myrepo",
                       src_root=tmp_path / "src")
        assert "links" in stats
        assert isinstance(stats["links"], int)
        assert stats["links"] >= 0

    def test_parent_child_links_generated(self, tmp_path):
        src_dir = self._setup_two_files(tmp_path)
        wiki_dir = tmp_path / "wiki"
        ingest([src_dir], wiki_dir, "myrepo", src_root=tmp_path / "src",
               overwrite=True)

        # Alpha's entity file should have a "Contains" link for any method,
        # and Alpha itself should have a "Part of" link back to the module.
        alpha_file = wiki_dir / "myrepo.mypkg.parent.Alpha.md"
        assert alpha_file.exists()
        alpha_md = alpha_file.read_text()
        assert "**Part of**" in alpha_md
        assert "[[myrepo.mypkg.parent|" in alpha_md

    def test_import_links_generated(self, tmp_path):
        src_dir = self._setup_two_files(tmp_path)
        wiki_dir = tmp_path / "wiki"
        ingest([src_dir], wiki_dir, "myrepo", src_root=tmp_path / "src",
               overwrite=True)

        # child module imports parent — child's entity file should have an Imports link
        child_mod_file = wiki_dir / "myrepo.mypkg.child.md"
        assert child_mod_file.exists()
        child_md = child_mod_file.read_text()
        assert "**Imports**" in child_md
        assert "[[myrepo.mypkg.parent|" in child_md

    def test_no_self_import_links(self, tmp_path):
        """A module must not link to itself via imports."""
        src_dir = tmp_path / "src" / "mypkg"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "selfref.py").write_bytes(b"import os\nimport sys\n")
        wiki_dir = tmp_path / "wiki"
        ingest([src_dir], wiki_dir, "myrepo", src_root=tmp_path / "src")
        mod_file = wiki_dir / "myrepo.mypkg.selfref.md"
        md = mod_file.read_text()
        # os/sys are not in this repo — no Imports link expected
        assert "[[myrepo.mypkg.selfref|" not in md
