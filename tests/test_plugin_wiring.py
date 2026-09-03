"""Tests for otaman_core.plugin_wiring — doctor WARNs on plugin-tree wiring gaps.

ce-bootstrap-plugin-wiring 1.2.
"""

from __future__ import annotations

from pathlib import Path

from otaman_core.plugin_wiring import (
    DEFAULT_PLUGIN_TREE_RELPATH,
    check_plugin_wiring,
    default_plugin_tree,
    resolve_plugin_wiring,
)

# --- pure check_plugin_wiring -------------------------------------------------


def test_vendored_tree_present_but_key_absent_warns() -> None:
    """Scenario: vendored tree, key absent → WARN naming the tree + fix."""
    findings = check_plugin_wiring(
        vendored_tree_present=True,
        plugin_dir=None,
        vendored_tree_path="/home/acme/.otaman/otaman-plugin-tree",
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.level == "warn"
    assert "/home/acme/.otaman/otaman-plugin-tree" in f.message
    assert "plugin_dir" in f.message
    assert "reconcile" in f.message


def test_key_set_directory_missing_warns() -> None:
    """Scenario: key set, directory missing → WARN naming the dangling path."""
    findings = check_plugin_wiring(
        vendored_tree_present=False,
        plugin_dir="/nope/plugin-tree",
        plugin_dir_present=False,
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.level == "warn"
    assert "/nope/plugin-tree" in f.message


def test_wired_and_present_is_healthy() -> None:
    """Tree present + plugin_dir set & present → no findings."""
    findings = check_plugin_wiring(
        vendored_tree_present=True,
        plugin_dir="/home/acme/.otaman/otaman-plugin-tree",
        plugin_dir_present=True,
    )
    assert findings == []


def test_no_tree_no_key_is_healthy() -> None:
    """Neither vendored tree nor plugin_dir → CE-without-plugin, no findings."""
    findings = check_plugin_wiring(vendored_tree_present=False, plugin_dir=None)
    assert findings == []


def test_key_set_and_present_ignores_vendored_absent() -> None:
    """A wired+present plugin_dir is healthy even if the default tree path is absent.

    The operator may point plugin_dir at a non-default location; presence of the
    wired directory is what matters, not the default tree path.
    """
    findings = check_plugin_wiring(
        vendored_tree_present=False,
        plugin_dir="/custom/tree",
        plugin_dir_present=True,
    )
    assert findings == []


def test_tree_path_omitted_still_warns_without_path_text() -> None:
    """Missing vendored_tree_path still WARNs (just without the ' at <path>')."""
    findings = check_plugin_wiring(vendored_tree_present=True, plugin_dir=None)
    assert len(findings) == 1
    assert " at " not in findings[0].message.split("fix:")[0]


# --- resolve_plugin_wiring (disk-facing) --------------------------------------


def test_default_plugin_tree_path() -> None:
    home = Path("/home/acme")
    assert default_plugin_tree(home) == home / DEFAULT_PLUGIN_TREE_RELPATH


def test_resolve_unwired_tree_on_disk(tmp_path: Path) -> None:
    """Vendored tree exists on disk, config has no plugin_dir → WARN."""
    tree = tmp_path / DEFAULT_PLUGIN_TREE_RELPATH
    tree.mkdir(parents=True)
    findings = resolve_plugin_wiring(
        {"runner": {"agent_bootstrap": {"mcp_config": ".mcp.json"}}},
        home=tmp_path,
    )
    assert len(findings) == 1
    assert str(tree) in findings[0].message


def test_resolve_dangling_plugin_dir(tmp_path: Path) -> None:
    """plugin_dir points at a missing directory → WARN."""
    findings = resolve_plugin_wiring(
        {"runner": {"agent_bootstrap": {"plugin_dir": str(tmp_path / "gone")}}},
        home=tmp_path,
    )
    assert len(findings) == 1
    assert "gone" in findings[0].message


def test_resolve_wired_and_present_is_clean(tmp_path: Path) -> None:
    """plugin_dir set to an existing dir → healthy."""
    tree = tmp_path / DEFAULT_PLUGIN_TREE_RELPATH
    tree.mkdir(parents=True)
    findings = resolve_plugin_wiring(
        {"runner": {"agent_bootstrap": {"plugin_dir": str(tree)}}},
        home=tmp_path,
    )
    assert findings == []


def test_resolve_relative_plugin_dir_against_platform_dir(tmp_path: Path) -> None:
    """A relative plugin_dir resolves against platform_dir when given."""
    platform_dir = tmp_path / "proj"
    (platform_dir / "vendor" / "tree").mkdir(parents=True)
    findings = resolve_plugin_wiring(
        {"runner": {"agent_bootstrap": {"plugin_dir": "vendor/tree"}}},
        home=tmp_path,
        platform_dir=platform_dir,
    )
    assert findings == []


def test_resolve_tilde_and_env_expansion(tmp_path: Path, monkeypatch) -> None:
    """~ and $VAR in plugin_dir are expanded before the existence check."""
    tree = tmp_path / "expanded-tree"
    tree.mkdir()
    monkeypatch.setenv("OTAMAN_TEST_TREE", str(tree))
    findings = resolve_plugin_wiring(
        {"runner": {"agent_bootstrap": {"plugin_dir": "$OTAMAN_TEST_TREE"}}},
        home=tmp_path,
    )
    assert findings == []


def test_resolve_missing_runner_block_no_tree_is_clean(tmp_path: Path) -> None:
    """No runner block and no vendored tree → nothing to warn about."""
    assert resolve_plugin_wiring({}, home=tmp_path) == []


def test_resolve_missing_runner_block_with_tree_warns(tmp_path: Path) -> None:
    """No runner block but the tree is vendored → unwired WARN."""
    (tmp_path / DEFAULT_PLUGIN_TREE_RELPATH).mkdir(parents=True)
    findings = resolve_plugin_wiring({}, home=tmp_path)
    assert len(findings) == 1
    assert findings[0].level == "warn"


def test_resolve_empty_plugin_dir_string_treated_as_absent(tmp_path: Path) -> None:
    """An empty plugin_dir string is treated as absent (unwired), not dangling."""
    (tmp_path / DEFAULT_PLUGIN_TREE_RELPATH).mkdir(parents=True)
    findings = resolve_plugin_wiring(
        {"runner": {"agent_bootstrap": {"plugin_dir": ""}}},
        home=tmp_path,
    )
    assert len(findings) == 1
    assert "absent" in findings[0].message
