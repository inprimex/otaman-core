"""Tests for otaman_core.owner_paths — RepoConfig, resolution, validation.

Covers monorepo-path-ownership tasks 1.1–1.4.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_core.owner_paths import (
    OwnerPathsError,
    PlatformConfig,
    RepoConfig,
    _match_path,
    load_platform_config,
    parse_platform_config,
    resolve_owner_for_path,
    resolve_owners_for_paths,
    validate_owner_paths,
)

# ---------------------------------------------------------------------------
# parse_platform_config


class TestParseHappyPath:
    def test_no_owner_paths(self):
        cfg = parse_platform_config({
            "repos": [{"name": "core", "owner": "core-agent"}],
        })
        assert len(cfg.repos) == 1
        r = cfg.repos[0]
        assert r.name == "core"
        assert r.owner == "core-agent"
        assert r.owner_paths == {}

    def test_owner_paths_hyphenated(self):
        cfg = parse_platform_config({
            "repos": [{
                "name": "mono",
                "owner": "root-agent",
                "owner-paths": {
                    "apps/web/**": "web-agent",
                    "apps/api/**": "api-agent",
                },
            }],
        })
        r = cfg.repos[0]
        assert r.owner_paths == {
            "apps/web/**": "web-agent",
            "apps/api/**": "api-agent",
        }

    def test_owner_paths_underscored_alias(self):
        cfg = parse_platform_config({
            "repos": [{
                "name": "mono",
                "owner": "root",
                "owner_paths": {"apps/x/**": "x-agent"},
            }],
        })
        assert cfg.repos[0].owner_paths == {"apps/x/**": "x-agent"}

    def test_empty_repos_list(self):
        cfg = parse_platform_config({"repos": []})
        assert cfg.repos == []

    def test_missing_repos_key(self):
        cfg = parse_platform_config({})
        assert cfg.repos == []


class TestParseErrors:
    def test_repos_not_list(self):
        with pytest.raises(OwnerPathsError, match="repos"):
            parse_platform_config({"repos": {"a": 1}})

    def test_repo_not_mapping(self):
        with pytest.raises(OwnerPathsError, match="repos\\[0\\]"):
            parse_platform_config({"repos": ["just-a-string"]})

    def test_missing_name(self):
        with pytest.raises(OwnerPathsError, match="name"):
            parse_platform_config({"repos": [{"owner": "x"}]})

    def test_missing_owner(self):
        with pytest.raises(OwnerPathsError, match="owner"):
            parse_platform_config({"repos": [{"name": "x"}]})

    def test_owner_paths_not_mapping(self):
        with pytest.raises(OwnerPathsError, match="owner-paths"):
            parse_platform_config({"repos": [{
                "name": "x", "owner": "x-agent", "owner-paths": ["nope"],
            }]})

    def test_empty_glob_key(self):
        with pytest.raises(OwnerPathsError, match="non-empty strings"):
            parse_platform_config({"repos": [{
                "name": "x", "owner": "x-agent", "owner-paths": {"": "agent"},
            }]})

    def test_empty_agent_value(self):
        with pytest.raises(OwnerPathsError, match="non-empty agent name"):
            parse_platform_config({"repos": [{
                "name": "x", "owner": "x-agent", "owner-paths": {"a/**": ""},
            }]})


# ---------------------------------------------------------------------------
# load_platform_config


class TestLoadPlatformConfig:
    def test_missing_file_returns_none(self, tmp_path: Path):
        assert load_platform_config(tmp_path / "nope.yaml") is None

    def test_loads_repos(self, tmp_path: Path):
        p = tmp_path / "platform.yaml"
        p.write_text(
            "project: x\nversion: '1.0'\n"
            "repos:\n"
            "  - name: mono\n"
            "    owner: root-agent\n"
            "    owner-paths:\n"
            "      apps/web/**: web-agent\n",
            encoding="utf-8",
        )
        cfg = load_platform_config(p)
        assert cfg is not None
        assert cfg.repos[0].owner_paths == {"apps/web/**": "web-agent"}

    def test_corrupt_yaml_returns_none(self, tmp_path: Path):
        p = tmp_path / "platform.yaml"
        p.write_text("not: valid: yaml: [", encoding="utf-8")
        assert load_platform_config(p) is None


# ---------------------------------------------------------------------------
# _match_path — glob semantics


class TestMatchPath:
    def test_double_star_matches_all_depths(self):
        assert _match_path("apps/web/index.tsx", "apps/web/**")
        assert _match_path("apps/web/components/Btn.tsx", "apps/web/**")

    def test_double_star_does_not_match_wrong_root(self):
        assert not _match_path("apps/api/server.py", "apps/web/**")

    def test_single_star_within_segment(self):
        assert _match_path("packages/x/index.ts", "packages/*/index.ts")
        assert not _match_path("packages/x/sub/index.ts", "packages/*/index.ts")

    def test_basename_pattern_at_any_depth(self):
        assert _match_path("README.md", "*.md")
        assert _match_path("docs/guide/intro.md", "*.md")

    def test_anchored_root_pattern(self):
        assert _match_path("README.md", "/README.md")
        assert not _match_path("docs/README.md", "/README.md")

    def test_exact_path(self):
        assert _match_path("apps/web/package.json", "apps/web/package.json")
        assert not _match_path("apps/api/package.json", "apps/web/package.json")


# ---------------------------------------------------------------------------
# resolve_owner_for_path


@pytest.fixture
def mono_cfg() -> PlatformConfig:
    return parse_platform_config({
        "repos": [{
            "name": "mono",
            "owner": "root-agent",
            "owner-paths": {
                "apps/web/**": "web-agent",
                "apps/api/**": "api-agent",
                "packages/shared/**": "shared-agent",
                "apps/web/admin/**": "admin-agent",  # more specific than apps/web/**
            },
        }],
    })


class TestResolveOwnerForPath:
    def test_root_fallback_when_no_match(self, mono_cfg):
        assert resolve_owner_for_path(mono_cfg, "mono", "tools/lint.py") == "root-agent"

    def test_simple_match(self, mono_cfg):
        assert resolve_owner_for_path(mono_cfg, "mono", "apps/api/server.py") == "api-agent"

    def test_specificity_tie_break(self, mono_cfg):
        """Longer glob string wins. admin/** beats web/**."""
        assert resolve_owner_for_path(
            mono_cfg, "mono", "apps/web/admin/dashboard.tsx",
        ) == "admin-agent"

    def test_less_specific_when_specific_doesnt_match(self, mono_cfg):
        assert resolve_owner_for_path(
            mono_cfg, "mono", "apps/web/landing.tsx",
        ) == "web-agent"

    def test_repo_with_no_owner_paths_returns_owner(self):
        cfg = parse_platform_config({
            "repos": [{"name": "core", "owner": "core-agent"}],
        })
        assert resolve_owner_for_path(cfg, "core", "any/path.py") == "core-agent"

    def test_unknown_repo_raises(self, mono_cfg):
        with pytest.raises(ValueError, match="not in platform.yaml"):
            resolve_owner_for_path(mono_cfg, "ghost", "foo.py")


# ---------------------------------------------------------------------------
# resolve_owners_for_paths


class TestResolveOwnersForPaths:
    def test_multi_path_mapping(self, mono_cfg):
        result = resolve_owners_for_paths(
            mono_cfg, "mono",
            ["apps/web/page.tsx", "apps/api/main.py", "tools/lint.py"],
        )
        assert result == {
            "apps/web/page.tsx": "web-agent",
            "apps/api/main.py": "api-agent",
            "tools/lint.py": "root-agent",
        }

    def test_empty_paths_yields_empty(self, mono_cfg):
        assert resolve_owners_for_paths(mono_cfg, "mono", []) == {}


# ---------------------------------------------------------------------------
# validate_owner_paths


class TestValidateOwnerPaths:
    def test_unknown_agent_reports_error(self):
        cfg = parse_platform_config({
            "repos": [{
                "name": "mono", "owner": "root-agent",
                "owner-paths": {"apps/x/**": "ghost-agent"},
            }],
        })
        issues = validate_owner_paths(cfg, known_agents={"root-agent"})
        assert any(
            i.severity == "error" and "ghost-agent" in i.message for i in issues
        )

    def test_known_agents_no_error(self):
        cfg = parse_platform_config({
            "repos": [{
                "name": "mono", "owner": "root-agent",
                "owner-paths": {"apps/web/**": "web-agent"},
            }],
        })
        issues = validate_owner_paths(cfg, known_agents={"root-agent", "web-agent"})
        assert all(i.severity != "error" for i in issues)

    def test_overlap_same_length_emits_warning(self):
        """Two patterns of equal specificity both matching the same probe."""
        cfg = parse_platform_config({
            "repos": [{
                "name": "mono", "owner": "root",
                "owner-paths": {"apps/*/web": "a", "apps/*/api": "b"},
            }],
        })
        # No overlap here — different last segment. Should NOT warn.
        issues = validate_owner_paths(cfg, known_agents={"root", "a", "b"})
        assert all(i.severity != "warning" for i in issues)

    def test_actual_overlap_warns(self):
        # Dict dedup means we can't test true duplicate keys. Use two different
        # patterns of equal length that match the same probe.
        cfg2 = parse_platform_config({
            "repos": [{
                "name": "mono", "owner": "root",
                "owner-paths": {
                    "apps/x/**": "a",
                    "apps/?/**": "b",  # same length, both match apps/x/anything
                },
            }],
        })
        issues = validate_owner_paths(cfg2, known_agents={"root", "a", "b"})
        warnings = [i for i in issues if i.severity == "warning"]
        assert warnings, f"expected overlap warning, got {issues}"

    def test_no_owner_paths_no_issues(self):
        cfg = parse_platform_config({
            "repos": [{"name": "core", "owner": "core-agent"}],
        })
        assert validate_owner_paths(cfg, known_agents={"core-agent"}) == []


# ---------------------------------------------------------------------------
# Dataclass surface


class TestPlatformConfig:
    def test_get_repo(self):
        cfg = PlatformConfig(repos=[
            RepoConfig(name="a", owner="a-agent"),
            RepoConfig(name="b", owner="b-agent"),
        ])
        assert cfg.get_repo("a").name == "a"
        assert cfg.get_repo("missing") is None

    def test_frozen(self):
        rc = RepoConfig(name="x", owner="x-agent")
        with pytest.raises(Exception):
            rc.name = "y"  # type: ignore[misc]
