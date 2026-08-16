"""Tests for the bus-test-isolation primitive: sandbox, sentinel, env validation.

Covers otaman_core.testing (1.1) + the resolver hardening (1.1 sentinel, 1.2
env-step validation). Scenarios mirror the bus-write-integrity spec.
"""

from __future__ import annotations

import pytest

from otaman_core._resolve import (
    TEST_MODE_ENV,
    RootResolutionError,
    _under_os_tmp,
    find_maestro_root,
)
from otaman_core.testing import STRIPPED_VARS, make_program_sandbox


class TestSandbox:
    def test_make_program_sandbox_shape(self, tmp_path):
        root = make_program_sandbox(tmp_path / "sb")
        assert (root / "platform.yaml").is_file()
        assert (root / ".agents" / "bus" / "active").is_dir()

    def test_sandbox_is_a_resolvable_program_root(self, tmp_path, monkeypatch):
        # env step requires a program marker (platform.yaml); the sandbox has it
        root = make_program_sandbox(tmp_path / "sb")
        monkeypatch.delenv("MAESTRO_ROOT", raising=False)
        monkeypatch.setenv("OTAMAN_ROOT", str(root))
        assert find_maestro_root(tmp_path / "elsewhere") == root.resolve()


class TestIsolateBusFixture:
    def test_autouse_strips_vars_and_sets_sentinel(self):
        # isolate_bus is autouse — by the time this test runs the env is clean
        import os

        for var in STRIPPED_VARS:
            if var == "OTAMAN_ROOT":
                continue  # fixture re-points this at the sandbox
            assert var not in os.environ, f"{var} should be stripped in tests"
        assert os.environ.get(TEST_MODE_ENV) == "1"

    def test_otaman_root_points_at_sandbox(self, isolate_bus):
        import os

        assert os.environ["OTAMAN_ROOT"] == str(isolate_bus)
        assert (isolate_bus / "platform.yaml").is_file()


class TestTmpTreeHelper:
    def test_under_os_tmp_true_for_tmp_path(self, tmp_path):
        assert _under_os_tmp(tmp_path) is True

    def test_under_os_tmp_false_for_non_tmp_path(self):
        from pathlib import Path

        # an absolute path clearly outside the OS tmp tree
        assert _under_os_tmp(Path("/home/someone/orgs/acme")) is False


class TestSentinel:
    def test_sentinel_refuses_real_root(self, tmp_path, monkeypatch):
        """OTAMAN_TEST_MODE set + a resolvable real (non-tmp) root -> loud refusal."""
        # Build a program root OUTSIDE tmp by faking a non-tmp resolution:
        # point OTAMAN_ROOT at a program root under a dir we pretend is real.
        # Simplest: monkeypatch _under_os_tmp to treat our sandbox as non-tmp.
        root = make_program_sandbox(tmp_path / "sb")
        monkeypatch.delenv("MAESTRO_ROOT", raising=False)
        monkeypatch.setenv("OTAMAN_ROOT", str(root))
        monkeypatch.setenv(TEST_MODE_ENV, "1")
        monkeypatch.setattr("otaman_core._resolve._under_os_tmp", lambda p: False)
        with pytest.raises(RootResolutionError, match=TEST_MODE_ENV):
            find_maestro_root(tmp_path / "elsewhere")

    def test_sentinel_allows_tmp_root(self, tmp_path, monkeypatch):
        root = make_program_sandbox(tmp_path / "sb")
        monkeypatch.delenv("MAESTRO_ROOT", raising=False)
        monkeypatch.setenv("OTAMAN_ROOT", str(root))
        monkeypatch.setenv(TEST_MODE_ENV, "1")
        # real _under_os_tmp -> tmp_path is under /tmp -> allowed
        assert find_maestro_root(tmp_path / "elsewhere") == root.resolve()

    def test_none_result_is_fine_under_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OTAMAN_ROOT", raising=False)
        monkeypatch.delenv("MAESTRO_ROOT", raising=False)
        monkeypatch.setenv(TEST_MODE_ENV, "1")
        orphan = tmp_path / "orphan"
        orphan.mkdir()
        assert find_maestro_root(orphan) is None


class TestEnvStepValidation:
    def test_org_level_env_root_rejected(self, tmp_path, monkeypatch):
        """Bare .agents dir (org-level shape, no platform.yaml) -> rejected."""
        org = tmp_path / "orgs" / "acme"
        (org / ".agents").mkdir(parents=True)
        monkeypatch.delenv("MAESTRO_ROOT", raising=False)
        monkeypatch.setenv("OTAMAN_ROOT", str(org))
        with pytest.raises(RootResolutionError, match=r"OTAMAN_ROOT.*acme"):
            find_maestro_root(tmp_path / "somewhere")

    def test_program_root_env_accepted(self, tmp_path, monkeypatch):
        prog = make_program_sandbox(tmp_path / "prog")
        monkeypatch.delenv("MAESTRO_ROOT", raising=False)
        monkeypatch.setenv("OTAMAN_ROOT", str(prog))
        assert find_maestro_root(tmp_path / "somewhere") == prog.resolve()
