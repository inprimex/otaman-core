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
    load_platform_config,
    parse_platform_config,
    path_matches,
    resolve_owner_for_cwd,
    resolve_owner_for_path,
    resolve_owners_for_paths,
    validate_owner_paths,
)

# ---------------------------------------------------------------------------
# parse_platform_config


class TestParseHappyPath:
    def test_no_owner_paths(self):
        cfg = parse_platform_config(
            {
                "repos": [{"name": "core", "owner": "core-agent"}],
            }
        )
        assert len(cfg.repos) == 1
        r = cfg.repos[0]
        assert r.name == "core"
        assert r.owner == "core-agent"
        assert r.owner_paths == {}

    def test_owner_paths_hyphenated(self):
        cfg = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "mono",
                        "owner": "root-agent",
                        "owner-paths": {
                            "apps/web/**": "web-agent",
                            "apps/api/**": "api-agent",
                        },
                    }
                ],
            }
        )
        r = cfg.repos[0]
        assert r.owner_paths == {
            "apps/web/**": "web-agent",
            "apps/api/**": "api-agent",
        }

    def test_owner_paths_underscored_alias(self):
        cfg = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "mono",
                        "owner": "root",
                        "owner_paths": {"apps/x/**": "x-agent"},
                    }
                ],
            }
        )
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
            parse_platform_config(
                {
                    "repos": [
                        {
                            "name": "x",
                            "owner": "x-agent",
                            "owner-paths": ["nope"],
                        }
                    ]
                }
            )

    def test_empty_glob_key(self):
        with pytest.raises(OwnerPathsError, match="non-empty strings"):
            parse_platform_config(
                {
                    "repos": [
                        {
                            "name": "x",
                            "owner": "x-agent",
                            "owner-paths": {"": "agent"},
                        }
                    ]
                }
            )

    def test_empty_agent_value(self):
        with pytest.raises(OwnerPathsError, match="non-empty agent name"):
            parse_platform_config(
                {
                    "repos": [
                        {
                            "name": "x",
                            "owner": "x-agent",
                            "owner-paths": {"a/**": ""},
                        }
                    ]
                }
            )


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
# path_matches — glob semantics


class TestMatchPath:
    def test_double_star_matches_all_depths(self):
        assert path_matches("apps/web/index.tsx", "apps/web/**")
        assert path_matches("apps/web/components/Btn.tsx", "apps/web/**")

    def test_double_star_does_not_match_wrong_root(self):
        assert not path_matches("apps/api/server.py", "apps/web/**")

    def test_single_star_within_segment(self):
        assert path_matches("packages/x/index.ts", "packages/*/index.ts")
        assert not path_matches("packages/x/sub/index.ts", "packages/*/index.ts")

    def test_single_star_stays_in_its_segment(self):
        """Ruled semantics (shared-logic-single-home 1.4): `*` does not cross `/`,
        so `*.md` matches a root-level .md but NOT one nested under a directory —
        that needs `**`. (Previously `*.md` matched at any depth.)"""
        assert path_matches("README.md", "*.md")
        assert not path_matches("docs/guide/intro.md", "*.md")
        assert path_matches("docs/guide/intro.md", "**/*.md")

    def test_bare_star_does_not_cross_slash(self):
        """The ruling's own probe: `*` matches a single root segment, not `a/b.py`."""
        assert path_matches("a.py", "*")
        assert not path_matches("a/b.py", "*")

    def test_bare_directory_means_its_subtree(self):
        """The ruling's own probe: a wildcard-free name matches the exact path or
        its whole subtree (`src` == `src/**`), anchored at the repo root."""
        assert path_matches("src", "src")
        assert path_matches("src/x.py", "src")
        assert not path_matches("foo/src/x.py", "src")  # anchored — not any depth

    def test_anchored_root_pattern(self):
        assert path_matches("README.md", "/README.md")
        assert not path_matches("docs/README.md", "/README.md")

    def test_exact_path(self):
        assert path_matches("apps/web/package.json", "apps/web/package.json")
        assert not path_matches("apps/api/package.json", "apps/web/package.json")

    def test_wildcard_free_directory_prefix_owns_subtree(self):
        assert path_matches("apps/web/src/App.tsx", "apps/web")
        assert path_matches("apps/web", "apps/web")
        assert not path_matches("apps/webx/y", "apps/web")  # prefix must be a full segment


# ---------------------------------------------------------------------------
# resolve_owner_for_path


@pytest.fixture
def mono_cfg() -> PlatformConfig:
    return parse_platform_config(
        {
            "repos": [
                {
                    "name": "mono",
                    "owner": "root-agent",
                    "owner-paths": {
                        "apps/web/**": "web-agent",
                        "apps/api/**": "api-agent",
                        "packages/shared/**": "shared-agent",
                        "apps/web/admin/**": "admin-agent",  # more specific than apps/web/**
                    },
                }
            ],
        }
    )


class TestResolveOwnerForPath:
    def test_root_fallback_when_no_match(self, mono_cfg):
        assert resolve_owner_for_path(mono_cfg, "mono", "tools/lint.py") == "root-agent"

    def test_simple_match(self, mono_cfg):
        assert resolve_owner_for_path(mono_cfg, "mono", "apps/api/server.py") == "api-agent"

    def test_specificity_tie_break(self, mono_cfg):
        """Longer glob string wins. admin/** beats web/**."""
        assert (
            resolve_owner_for_path(
                mono_cfg,
                "mono",
                "apps/web/admin/dashboard.tsx",
            )
            == "admin-agent"
        )

    def test_less_specific_when_specific_doesnt_match(self, mono_cfg):
        assert (
            resolve_owner_for_path(
                mono_cfg,
                "mono",
                "apps/web/landing.tsx",
            )
            == "web-agent"
        )

    def test_repo_with_no_owner_paths_returns_owner(self):
        cfg = parse_platform_config(
            {
                "repos": [{"name": "core", "owner": "core-agent"}],
            }
        )
        assert resolve_owner_for_path(cfg, "core", "any/path.py") == "core-agent"

    def test_unknown_repo_raises(self, mono_cfg):
        with pytest.raises(ValueError, match="not in platform.yaml"):
            resolve_owner_for_path(mono_cfg, "ghost", "foo.py")


# ---------------------------------------------------------------------------
# resolve_owners_for_paths


class TestResolveOwnersForPaths:
    def test_multi_path_mapping(self, mono_cfg):
        result = resolve_owners_for_paths(
            mono_cfg,
            "mono",
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
        cfg = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "mono",
                        "owner": "root-agent",
                        "owner-paths": {"apps/x/**": "ghost-agent"},
                    }
                ],
            }
        )
        issues = validate_owner_paths(cfg, known_agents={"root-agent"})
        assert any(i.severity == "error" and "ghost-agent" in i.message for i in issues)

    def test_known_agents_no_error(self):
        cfg = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "mono",
                        "owner": "root-agent",
                        "owner-paths": {"apps/web/**": "web-agent"},
                    }
                ],
            }
        )
        issues = validate_owner_paths(cfg, known_agents={"root-agent", "web-agent"})
        assert all(i.severity != "error" for i in issues)

    def test_overlap_same_length_emits_warning(self):
        """Two patterns of equal specificity both matching the same probe."""
        cfg = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "mono",
                        "owner": "root",
                        "owner-paths": {"apps/*/web": "a", "apps/*/api": "b"},
                    }
                ],
            }
        )
        # No overlap here — different last segment. Should NOT warn.
        issues = validate_owner_paths(cfg, known_agents={"root", "a", "b"})
        assert all(i.severity != "warning" for i in issues)

    def test_actual_overlap_warns(self):
        # Dict dedup means we can't test true duplicate keys. Use two different
        # patterns of equal length that match the same probe.
        cfg2 = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "mono",
                        "owner": "root",
                        "owner-paths": {
                            "apps/x/**": "a",
                            "apps/?/**": "b",  # same length, both match apps/x/anything
                        },
                    }
                ],
            }
        )
        issues = validate_owner_paths(cfg2, known_agents={"root", "a", "b"})
        warnings = [i for i in issues if i.severity == "warning"]
        assert warnings, f"expected overlap warning, got {issues}"

    def test_no_owner_paths_no_issues(self):
        cfg = parse_platform_config(
            {
                "repos": [{"name": "core", "owner": "core-agent"}],
            }
        )
        assert validate_owner_paths(cfg, known_agents={"core-agent"}) == []


# ---------------------------------------------------------------------------
# Dataclass surface


class TestPlatformConfig:
    def test_get_repo(self):
        cfg = PlatformConfig(
            repos=[
                RepoConfig(name="a", owner="a-agent"),
                RepoConfig(name="b", owner="b-agent"),
            ]
        )
        assert cfg.get_repo("a").name == "a"
        assert cfg.get_repo("missing") is None

    def test_frozen(self):
        rc = RepoConfig(name="x", owner="x-agent")
        with pytest.raises(AttributeError):
            rc.name = "y"  # type: ignore[misc]


class TestRepoPathField:
    def test_path_captured(self):
        cfg = parse_platform_config(
            {"repos": [{"name": "core", "owner": "core-agent", "path": "../otaman-core"}]}
        )
        assert cfg.repos[0].path == "../otaman-core"

    def test_path_defaults_empty(self):
        cfg = parse_platform_config({"repos": [{"name": "core", "owner": "core-agent"}]})
        assert cfg.repos[0].path == ""


class TestResolveOwnerForCwd:
    def _platform(self):
        return parse_platform_config(
            {
                "repos": [
                    {"name": "core", "owner": "core-agent", "path": "../otaman-core"},
                    {
                        "name": "mono",
                        "owner": "root-agent",
                        "path": "../mono",
                        "owner-paths": {"apps/web/**": "web-agent"},
                    },
                    {"name": "no-path", "owner": "ghost-agent"},
                ]
            }
        )

    def test_cwd_in_repo_root(self, tmp_path):
        root = tmp_path / "meta"
        root.mkdir()
        (tmp_path / "otaman-core").mkdir()
        cwd = tmp_path / "otaman-core"
        assert resolve_owner_for_cwd(self._platform(), cwd, root) == "core-agent"

    def test_cwd_in_subdir_catchall(self, tmp_path):
        root = tmp_path / "meta"
        root.mkdir()
        sub = tmp_path / "otaman-core" / "src" / "pkg"
        sub.mkdir(parents=True)
        assert resolve_owner_for_cwd(self._platform(), sub, root) == "core-agent"

    def test_owner_paths_glob_override(self, tmp_path):
        root = tmp_path / "meta"
        root.mkdir()
        web = tmp_path / "mono" / "apps" / "web" / "ui"
        web.mkdir(parents=True)
        assert resolve_owner_for_cwd(self._platform(), web, root) == "web-agent"

    def test_cwd_outside_any_repo_is_none(self, tmp_path):
        root = tmp_path / "meta"
        root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        assert resolve_owner_for_cwd(self._platform(), outside, root) is None

    def test_repo_without_path_skipped(self, tmp_path):
        # 'no-path' repo has no path → never matches a cwd
        root = tmp_path / "meta"
        root.mkdir()
        outside = tmp_path / "whatever"
        outside.mkdir()
        assert resolve_owner_for_cwd(self._platform(), outside, root) is None


# ---------------------------------------------------------------------------
# 7th divergence (cli finding): `<dir>/**` owns the subtree, not `<dir>` itself


class TestDoubleStarVsBareDir:
    def test_double_star_owns_subtree_not_the_dir_itself(self):
        assert path_matches("apps/web/x.py", "apps/web/**")
        assert not path_matches("apps/web", "apps/web/**")

    def test_bare_dir_owns_both_the_dir_and_its_subtree(self):
        assert path_matches("apps/web", "apps/web")
        assert path_matches("apps/web/x.py", "apps/web")


# ---------------------------------------------------------------------------
# resolve_owner_for_cwd — worktree awareness (unblocks shared-logic 1.5)


def _make_worktree(tmp_path: Path, main_name: str, wt_name: str) -> tuple[Path, Path]:
    """A main repo checkout (with a real `.git/worktrees/<n>/` dir) and a sibling
    linked worktree whose `.git` FILE points into it — mirrors what git writes."""
    main = tmp_path / main_name
    worktrees_dir = main / ".git" / "worktrees" / "feature"
    worktrees_dir.mkdir(parents=True)
    wt = tmp_path / wt_name
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {worktrees_dir}\n", encoding="utf-8")
    return main, wt


class TestResolveOwnerForCwdWorktree:
    def test_worktree_root_resolves_to_repo_owner(self, tmp_path: Path):
        _main, wt = _make_worktree(tmp_path, "auth-service", "auth-service-feature")
        platform = PlatformConfig(
            repos=[RepoConfig(name="auth-service", owner="backend-agent", path="auth-service")]
        )
        assert resolve_owner_for_cwd(platform, wt, tmp_path) == "backend-agent"

    def test_nested_cwd_in_worktree_resolves_to_repo_owner(self, tmp_path: Path):
        _main, wt = _make_worktree(tmp_path, "auth-service", "auth-service-feature")
        platform = PlatformConfig(
            repos=[RepoConfig(name="auth-service", owner="backend-agent", path="auth-service")]
        )
        assert resolve_owner_for_cwd(platform, wt / "src", tmp_path) == "backend-agent"

    def test_worktrees_of_different_repos_resolve_to_different_owners(self, tmp_path: Path):
        _m1, wt1 = _make_worktree(tmp_path, "auth-service", "auth-service-wt")
        _m2, wt2 = _make_worktree(tmp_path, "web-app", "web-app-wt")
        platform = PlatformConfig(
            repos=[
                RepoConfig(name="auth-service", owner="backend-agent", path="auth-service"),
                RepoConfig(name="web-app", owner="frontend-agent", path="web-app"),
            ]
        )
        assert resolve_owner_for_cwd(platform, wt1, tmp_path) == "backend-agent"
        assert resolve_owner_for_cwd(platform, wt2, tmp_path) == "frontend-agent"

    def test_main_checkout_still_resolves_directly(self, tmp_path: Path):
        """Regression: the direct (non-worktree) path is unchanged and wins first."""
        main, _wt = _make_worktree(tmp_path, "auth-service", "auth-service-feature")
        platform = PlatformConfig(
            repos=[RepoConfig(name="auth-service", owner="backend-agent", path="auth-service")]
        )
        assert resolve_owner_for_cwd(platform, main, tmp_path) == "backend-agent"

    def test_dir_that_is_neither_repo_nor_worktree_returns_none(self, tmp_path: Path):
        (tmp_path / "unrelated").mkdir()
        platform = PlatformConfig(
            repos=[RepoConfig(name="auth-service", owner="backend-agent", path="auth-service")]
        )
        assert resolve_owner_for_cwd(platform, tmp_path / "unrelated", tmp_path) is None


# ---------------------------------------------------------------------------
# owner/name normalization (cli-agent 20260922T223409): a padded owner becomes
# a bus stem with spaces that reaches no recipient — strip at the parse home.


class TestOwnerNormalization:
    def test_parse_strips_padded_name_owner_and_agent(self):
        cfg = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "  svc  ",
                        "owner": "  backend-agent  ",
                        "owner-paths": {"apps/web/**": "  web-agent  "},
                    }
                ]
            }
        )
        (repo,) = cfg.repos
        assert repo.name == "svc"
        assert repo.owner == "backend-agent"
        assert repo.owner_paths == {"apps/web/**": "web-agent"}

    def test_padded_name_resolves(self):
        cfg = parse_platform_config({"repos": [{"name": "  svc  ", "owner": "  backend-agent  "}]})
        assert resolve_owner_for_path(cfg, "svc", "any/file.py") == "backend-agent"

    def test_resolved_owner_has_no_surrounding_whitespace(self):
        cfg = parse_platform_config(
            {
                "repos": [
                    {
                        "name": "svc",
                        "owner": "root-agent",
                        "owner-paths": {"apps/web/**": " web-agent "},
                    }
                ]
            }
        )
        assert resolve_owner_for_path(cfg, "svc", "apps/web/App.tsx") == "web-agent"

    def test_whitespace_only_owner_is_rejected_as_empty(self):
        with pytest.raises(OwnerPathsError, match="owner"):
            parse_platform_config({"repos": [{"name": "svc", "owner": "   "}]})

    def test_whitespace_only_name_is_rejected_as_empty(self):
        with pytest.raises(OwnerPathsError, match="name"):
            parse_platform_config({"repos": [{"name": "   ", "owner": "a-agent"}]})

    def test_resolve_defensively_strips_a_directly_built_repoconfig(self):
        # a RepoConfig constructed directly (not via parse) with a padded owner
        cfg = PlatformConfig(repos=[RepoConfig(name="svc", owner="  backend-agent  ")])
        assert resolve_owner_for_path(cfg, "svc", "x.py") == "backend-agent"
