"""Tests for otaman_core.bus.dispatch — recipient resolution.

Covers monorepo-path-ownership task 1.5.
"""

from __future__ import annotations

import pytest

from otaman_core.bus.dispatch import DispatchError, dispatch
from otaman_core.owner_paths import parse_platform_config


@pytest.fixture
def platform():
    return parse_platform_config({
        "repos": [
            {"name": "core", "owner": "core-agent"},
            {
                "name": "mono", "owner": "root-agent",
                "owner-paths": {
                    "apps/web/**": "web-agent",
                    "apps/api/**": "api-agent",
                    "packages/shared/**": "shared-agent",
                },
            },
        ],
    })


# ---------------------------------------------------------------------------
# Legacy `to:` routing (no path field)


class TestDispatchByTo:
    def test_single_recipient(self, platform):
        r = dispatch({"to": "core-agent"}, platform)
        assert r.mode == "to"
        assert r.recipients == ["core-agent"]
        assert r.per_path == {}

    def test_comma_separated_list(self, platform):
        r = dispatch({"to": "core-agent, web-agent"}, platform)
        assert r.recipients == ["core-agent", "web-agent"]

    def test_yaml_list(self, platform):
        r = dispatch({"to": ["core-agent", "api-agent"]}, platform)
        assert r.recipients == ["core-agent", "api-agent"]

    def test_human_and_all_passthrough(self, platform):
        assert dispatch({"to": "human"}, platform).recipients == ["human"]
        assert dispatch({"to": "all"}, platform).recipients == ["all"]


# ---------------------------------------------------------------------------
# Path-based routing


class TestDispatchByPath:
    def test_single_path_resolves(self, platform):
        r = dispatch({
            "repo": "mono",
            "path": "apps/api/server.py",
            "to": "human",  # to: is ignored when path: present
        }, platform)
        assert r.mode == "path"
        assert r.recipients == ["api-agent"]
        assert r.per_path == {"apps/api/server.py": "api-agent"}

    def test_fallback_to_root_owner(self, platform):
        r = dispatch({
            "repo": "mono", "path": "tools/lint.py",
        }, platform)
        assert r.recipients == ["root-agent"]

    def test_path_overrides_to(self, platform):
        """When path: is present, to: is ignored — recipient comes from owner-paths."""
        r = dispatch({
            "repo": "mono",
            "path": "apps/web/page.tsx",
            "to": "wrong-agent",
        }, platform)
        assert r.recipients == ["web-agent"]


class TestMulticast:
    def test_multi_path_distinct_owners(self, platform):
        r = dispatch({
            "repo": "mono",
            "path": ["apps/web/page.tsx", "apps/api/main.py"],
        }, platform)
        assert r.mode == "multicast"
        assert set(r.recipients) == {"web-agent", "api-agent"}
        assert r.per_path == {
            "apps/web/page.tsx": "web-agent",
            "apps/api/main.py": "api-agent",
        }

    def test_multi_path_same_owner_not_multicast(self, platform):
        r = dispatch({
            "repo": "mono",
            "path": ["apps/web/page.tsx", "apps/web/admin.tsx"],
        }, platform)
        assert r.mode == "path"
        assert r.recipients == ["web-agent"]

    def test_recipients_are_sorted(self, platform):
        """Stable order — callers depend on this for diff-friendliness."""
        r = dispatch({
            "repo": "mono",
            "path": ["apps/web/x", "apps/api/y", "packages/shared/z"],
        }, platform)
        assert r.recipients == sorted(r.recipients)


# ---------------------------------------------------------------------------
# Error paths


class TestDispatchErrors:
    def test_path_without_repo_raises(self, platform):
        with pytest.raises(DispatchError, match="repo"):
            dispatch({"path": "apps/api/x.py"}, platform)

    def test_unknown_repo_raises(self, platform):
        with pytest.raises(DispatchError, match="not in platform.yaml"):
            dispatch({"repo": "ghost", "path": "x.py"}, platform)

    def test_empty_path_list_raises(self, platform):
        with pytest.raises(DispatchError, match="empty list"):
            dispatch({"repo": "mono", "path": []}, platform)

    def test_invalid_path_type_raises(self, platform):
        with pytest.raises(DispatchError, match="string or a list"):
            dispatch({"repo": "mono", "path": 42}, platform)
