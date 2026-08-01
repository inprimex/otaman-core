"""Tests for otaman_core.pm_sync — protocol definition layer (JTBD-37)."""

from __future__ import annotations

import textwrap

import pytest

from otaman_core import pm_sync as pm

# ---------------------------------------------------------------------------
# PmAdapterCapabilities


class TestPmAdapterCapabilities:
    def test_instantiate_all_15_fields(self):
        caps = pm.PmAdapterCapabilities(
            issue_comments=True,
            custom_fields=True,
            custom_workflow=False,
            webhook_inbound=True,
            webhook_registration_api=True,
            user_creation_api=False,
            agent_identity_user=True,
            agent_identity_group=False,
            agent_identity_system_user=True,
            mcp_support=True,
            rest_api=True,
            native_assignee_metrics=False,
            project_hierarchy=True,
            github_url_field="cf_github_url",
            project_custom_fields_api=True,
        )
        assert caps.issue_comments is True
        assert caps.custom_fields is True
        assert caps.custom_workflow is False
        assert caps.webhook_inbound is True
        assert caps.webhook_registration_api is True
        assert caps.user_creation_api is False
        assert caps.agent_identity_user is True
        assert caps.agent_identity_group is False
        assert caps.agent_identity_system_user is True
        assert caps.mcp_support is True
        assert caps.rest_api is True
        assert caps.native_assignee_metrics is False
        assert caps.project_hierarchy is True
        assert caps.github_url_field == "cf_github_url"
        assert caps.project_custom_fields_api is True

    def test_github_url_field_none(self):
        caps = pm.PmAdapterCapabilities(
            issue_comments=False,
            custom_fields=False,
            custom_workflow=False,
            webhook_inbound=False,
            webhook_registration_api=False,
            user_creation_api=False,
            agent_identity_user=False,
            agent_identity_group=False,
            agent_identity_system_user=False,
            mcp_support=False,
            rest_api=False,
            native_assignee_metrics=False,
            project_hierarchy=False,
            github_url_field=None,
            project_custom_fields_api=False,
        )
        assert caps.github_url_field is None

    def test_frozen(self):
        caps = pm.PmAdapterCapabilities(
            issue_comments=True,
            custom_fields=True,
            custom_workflow=True,
            webhook_inbound=True,
            webhook_registration_api=True,
            user_creation_api=True,
            agent_identity_user=True,
            agent_identity_group=True,
            agent_identity_system_user=True,
            mcp_support=True,
            rest_api=True,
            native_assignee_metrics=True,
            project_hierarchy=True,
            github_url_field=None,
            project_custom_fields_api=True,
        )
        with pytest.raises((AttributeError, TypeError)):
            caps.rest_api = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PmSyncAdapter is runtime-checkable


class TestPmSyncAdapterRuntimeCheckable:
    def test_class_without_capabilities_is_not_adapter(self):
        class NotAnAdapter:
            pass

        # runtime_checkable checks for method presence, not actual Protocol conformance;
        # an object with none of the required attributes should not pass.
        assert not isinstance(NotAnAdapter(), pm.PmSyncAdapter)

    def test_minimal_conforming_class_passes_isinstance(self):
        """A class implementing all Protocol members passes isinstance check."""

        _caps = pm.PmAdapterCapabilities(
            issue_comments=True,
            custom_fields=True,
            custom_workflow=False,
            webhook_inbound=True,
            webhook_registration_api=True,
            user_creation_api=False,
            agent_identity_user=True,
            agent_identity_group=False,
            agent_identity_system_user=True,
            mcp_support=True,
            rest_api=True,
            native_assignee_metrics=False,
            project_hierarchy=True,
            github_url_field=None,
            project_custom_fields_api=True,
        )

        class ConcreteAdapter:
            @property
            def capabilities(self) -> pm.PmAdapterCapabilities:
                return _caps

            def provision_project(self, config):
                raise NotImplementedError

            def create_issue(self, spec_change):
                raise NotImplementedError

            def update_issue(self, issue_id, state):
                raise NotImplementedError

            def add_comment(self, issue_id, body):
                raise NotImplementedError

            def list_issues(self, filters):
                raise NotImplementedError

            def register_webhook(self, url, events):
                raise NotImplementedError

            def handle_inbound_event(self, payload):
                raise NotImplementedError

            def list_statuses(self):
                raise NotImplementedError

            def list_priorities(self):
                raise NotImplementedError

            def get_users(self):
                raise NotImplementedError

        assert isinstance(ConcreteAdapter(), pm.PmSyncAdapter)

    def test_protocol_declares_get_users(self):
        """human-roster task 4.1 — get_users() is part of the Protocol surface."""
        assert hasattr(pm.PmSyncAdapter, "get_users")

    def test_class_missing_get_users_is_not_adapter(self):
        """Removing get_users from a concrete adapter breaks isinstance check."""

        class PartialAdapter:
            @property
            def capabilities(self):
                return None

            def provision_project(self, config): ...
            def create_issue(self, spec_change): ...
            def update_issue(self, issue_id, state): ...
            def add_comment(self, issue_id, body): ...
            def list_issues(self, filters): ...
            def register_webhook(self, url, events): ...
            def handle_inbound_event(self, payload): ...
            def list_statuses(self): ...
            def list_priorities(self): ...
            # no get_users — should fail isinstance

        assert not isinstance(PartialAdapter(), pm.PmSyncAdapter)


# ---------------------------------------------------------------------------
# Registry round-trip


class TestAdapterRegistry:
    def setup_method(self):
        # Save and clear registry state between tests.
        self._saved = dict(pm._PM_ADAPTERS)
        pm._PM_ADAPTERS.clear()

    def teardown_method(self):
        pm._PM_ADAPTERS.clear()
        pm._PM_ADAPTERS.update(self._saved)

    def test_register_and_get_round_trip(self):
        class FakeAdapter:
            pass

        pm.register_pm_adapter("fake", FakeAdapter)
        assert pm.get_pm_adapter("fake") is FakeAdapter

    def test_list_adapters_sorted(self):
        class A:
            pass

        class B:
            pass

        pm.register_pm_adapter("zebra", A)
        pm.register_pm_adapter("alpha", B)
        names = pm.list_pm_adapters()
        assert names == ["alpha", "zebra"]

    def test_get_unknown_adapter_raises_key_error(self):
        with pytest.raises(KeyError, match="unknown PM adapter"):
            pm.get_pm_adapter("nonexistent")

    def test_list_empty_registry(self):
        assert pm.list_pm_adapters() == []


# ---------------------------------------------------------------------------
# load_pm_sync_config


class TestLoadPmSyncConfig:
    def test_returns_none_when_file_missing(self, tmp_path):
        result = pm.load_pm_sync_config(tmp_path / "platform.yaml")
        assert result is None

    def test_returns_none_when_pm_sync_block_absent(self, tmp_path):
        yaml_text = textwrap.dedent("""
            program:
              name: Acme
            repos:
              - name: acme-core
                path: ../acme-core
        """)
        p = tmp_path / "platform.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is None

    def test_returns_pm_sync_config_when_present(self, tmp_path):
        yaml_text = textwrap.dedent("""
            program:
              name: Otaman Platform

            pm-sync:
              provider: redmine
              base_url: https://pm.example.com
              identity_mode: system_user
              program_name: "Otaman Platform"
              program_key: OTAN
              per_repo: true
              exclude_repos:
                - otaman-docs
              webhook_target: https://hooks.otaman.io/pm
              project_map:
                otaman-core: 12
                otaman-plugin: 17
        """)
        p = tmp_path / "platform.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is not None
        assert isinstance(result, pm.PmSyncConfig)
        assert result.provider == "redmine"
        assert result.base_url == "https://pm.example.com"
        assert result.identity_mode == "system_user"
        assert result.program_name == "Otaman Platform"
        assert result.program_key == "OTAN"
        assert result.per_repo is True
        assert result.exclude_repos == ["otaman-docs"]
        assert result.webhook_target == "https://hooks.otaman.io/pm"
        assert result.project_map == {"otaman-core": 12, "otaman-plugin": 17}

    def test_returns_none_on_invalid_yaml(self, tmp_path):
        p = tmp_path / "platform.yaml"
        p.write_text("{ bad yaml: [unclosed", encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is None

    def test_returns_none_when_pm_sync_is_not_dict(self, tmp_path):
        yaml_text = "pm-sync: just-a-string\n"
        p = tmp_path / "platform.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is None

    def test_defaults_for_missing_optional_fields(self, tmp_path):
        yaml_text = textwrap.dedent("""
            pm-sync:
              provider: linear
              base_url: https://api.linear.app
              identity_mode: agent_user
              program_name: MyProg
              program_key: MP
        """)
        p = tmp_path / "platform.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is not None
        assert result.per_repo is False
        assert result.exclude_repos == []
        assert result.webhook_target == ""
        assert result.project_map == {}
        assert result.custom_fields is None


# ---------------------------------------------------------------------------
# pm-sync-rich-issues — tasks 2.1 + 2.2


class TestSpecChangeDescription:
    """Task 2.1 — SpecChange.description carries the proposal body."""

    def test_default_empty_string(self):
        sc = pm.SpecChange(
            change_name="example",
            title="Example",
            agent_name="core-agent",
            spec_path="openspec/changes/example",
            jtbd_id=None,
        )
        assert sc.description == ""

    def test_description_round_trips(self):
        body = "## Why\n\nBecause reasons.\n"
        sc = pm.SpecChange(
            change_name="example",
            title="Example",
            agent_name="core-agent",
            spec_path="openspec/changes/example",
            jtbd_id="JTBD-99",
            description=body,
        )
        assert sc.description == body


class TestPmSyncConfigCustomFields:
    """Task 2.2 — custom_fields override from pm-sync.custom-fields."""

    def _yaml_with(self, custom_fields_block: str) -> str:
        return textwrap.dedent(f"""
            pm-sync:
              provider: redmine
              base_url: https://pm.example.com
              identity_mode: system_user
              program_name: P
              program_key: P
{custom_fields_block}
        """)

    def test_absent_block_yields_none(self, tmp_path):
        p = tmp_path / "platform.yaml"
        p.write_text(self._yaml_with(""), encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is not None
        assert result.custom_fields is None

    def test_hyphenated_key(self, tmp_path):
        block = "              custom-fields:\n                jtbd-id: 4\n                otaman-agent: 5\n                spec-path: 6\n"
        p = tmp_path / "platform.yaml"
        p.write_text(self._yaml_with(block), encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is not None
        assert result.custom_fields == {"jtbd-id": 4, "otaman-agent": 5, "spec-path": 6}

    def test_underscored_alias(self, tmp_path):
        block = "              custom_fields:\n                jtbd-id: 4\n"
        p = tmp_path / "platform.yaml"
        p.write_text(self._yaml_with(block), encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is not None
        assert result.custom_fields == {"jtbd-id": 4}

    def test_empty_map_yields_none(self, tmp_path):
        """Empty map = no override; let the adapter auto-discover."""
        block = "              custom-fields: {}\n"
        p = tmp_path / "platform.yaml"
        p.write_text(self._yaml_with(block), encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is not None
        assert result.custom_fields is None

    def test_non_integer_id_falls_back_to_none(self, tmp_path):
        """Bad shape shouldn't break the whole config; auto-discovery picks up."""
        block = "              custom-fields:\n                jtbd-id: not-an-int\n"
        p = tmp_path / "platform.yaml"
        p.write_text(self._yaml_with(block), encoding="utf-8")
        result = pm.load_pm_sync_config(p)
        assert result is not None
        assert result.custom_fields is None

    def test_dataclass_default(self):
        cfg = pm.PmSyncConfig(
            provider="redmine",
            base_url="https://pm.example.com",
            identity_mode="system_user",
            program_name="P",
            program_key="P",
            per_repo=False,
            exclude_repos=[],
            webhook_target="",
            project_map={},
            status_map={},
            tracker="Task",
        )
        assert cfg.custom_fields is None
