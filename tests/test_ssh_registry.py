"""Tests for the thin ssh-agent socket registry (agent-credential-access 1.2).

Covers persistence round-trip, per-target isolation (no cross-contamination),
liveness via ssh-add exit codes, env-independent re-attach after a context
reset, prune, the thin spawn/load-key primitives, ~/.ssh/config Host-alias
resolution, and the values-never-exposed invariant (only locators on the model).

All ssh-agent / ssh-add shelling is stubbed via an injected runner — no live
agent required.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from otaman_core.ssh_registry import (
    AgentEntry,
    SshAgentRegistry,
    SshRegistryError,
    ssh_config_has_host,
    ssh_config_identity,
)


class FakeRunner:
    """Records invocations and replays scripted CompletedProcess results.

    ``responses`` maps the first argv token (e.g. "ssh-add", "ssh-agent") to a
    (returncode, stdout, stderr) triple; unmatched calls return rc 0.
    """

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv, env_overlay):
        self.calls.append((argv, dict(env_overlay)))
        rc, out, err = self.responses.get(argv[0], (0, "", ""))
        return subprocess.CompletedProcess(argv, rc, out, err)


def _live_socket(tmp_path: Path, name: str) -> str:
    sock = tmp_path / name
    sock.write_text("")  # a real file so Path.exists() is True
    return str(sock)


class TestPersistence:
    def test_round_trip(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run")
        reg.register(AgentEntry("sunflowers", "/keys/sun", str(tmp_path / "s.sock"), 4242))
        # a fresh instance reads the persisted map (env-independent)
        reg2 = SshAgentRegistry(tmp_path / "run")
        entry = reg2.get("sunflowers")
        assert entry == AgentEntry("sunflowers", "/keys/sun", str(tmp_path / "s.sock"), 4242)

    def test_absent_map_is_empty(self, tmp_path):
        assert SshAgentRegistry(tmp_path / "run").entries() == {}

    def test_corrupt_map_is_empty_not_crash(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run")
        reg._runtime_dir.mkdir(parents=True)
        reg.path.write_text("{not json")
        assert reg.load() == {}

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    def test_file_is_0600_and_dir_0700(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run")
        reg.register(AgentEntry("t", "/k", str(tmp_path / "t.sock")))
        assert (reg.path.stat().st_mode & 0o777) == 0o600
        assert (reg._runtime_dir.stat().st_mode & 0o777) == 0o700


class TestMapAccess:
    def test_register_get_remove(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run")
        reg.register(AgentEntry("a", "/k", "/s"))
        assert reg.get("a") is not None
        assert reg.remove("a") is True
        assert reg.get("a") is None
        assert reg.remove("a") is False

    def test_register_replaces(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run")
        reg.register(AgentEntry("a", "/k1", "/s1"))
        reg.register(AgentEntry("a", "/k2", "/s2"))
        assert reg.get("a").socket == "/s2"


class TestLiveness:
    def test_live_when_ssh_add_returns_0_or_1(self, tmp_path):
        sock = _live_socket(tmp_path, "a.sock")
        for rc in (0, 1):
            reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-add": (rc, "", "")}))
            assert reg.is_live(AgentEntry("a", "/k", sock)) is True

    def test_dead_when_ssh_add_returns_2(self, tmp_path):
        sock = _live_socket(tmp_path, "a.sock")
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-add": (2, "", "err")}))
        assert reg.is_live(AgentEntry("a", "/k", sock)) is False

    def test_dead_when_socket_missing(self, tmp_path):
        # missing socket short-circuits — runner not even consulted
        runner = FakeRunner({"ssh-add": (0, "", "")})
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)
        assert reg.is_live(AgentEntry("a", "/k", str(tmp_path / "gone.sock"))) is False
        assert runner.calls == []

    def test_is_live_probes_the_targets_own_socket(self, tmp_path):
        sock = _live_socket(tmp_path, "a.sock")
        runner = FakeRunner({"ssh-add": (0, "", "")})
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)
        reg.is_live(AgentEntry("a", "/k", sock))
        argv, env = runner.calls[0]
        assert argv == ["ssh-add", "-l"]
        assert env["SSH_AUTH_SOCK"] == sock


class TestReattach:
    def test_reattach_returns_live_entry(self, tmp_path):
        sock = _live_socket(tmp_path, "a.sock")
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-add": (1, "", "")}))
        reg.register(AgentEntry("a", "/k", sock))
        assert reg.reattach("a") is not None

    def test_reattach_dead_socket_returns_none(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-add": (2, "", "")}))
        reg.register(AgentEntry("a", "/k", str(tmp_path / "gone.sock")))
        assert reg.reattach("a") is None

    def test_reattach_unknown_returns_none(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner())
        assert reg.reattach("nope") is None

    def test_reattach_is_env_independent(self, tmp_path, monkeypatch):
        # Simulate a context reset: SSH_AUTH_SOCK gone from the environment.
        monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
        sock = _live_socket(tmp_path, "a.sock")
        SshAgentRegistry(tmp_path / "run").register(AgentEntry("a", "/k", sock))
        fresh = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-add": (0, "", "")}))
        assert fresh.reattach("a").socket == sock


class TestNoCrossContamination:
    def test_two_targets_two_sockets(self, tmp_path):
        sock_a = _live_socket(tmp_path, "a.sock")
        sock_b = _live_socket(tmp_path, "b.sock")
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-add": (0, "", "")}))
        reg.register(AgentEntry("srv-a", "/keys/a", sock_a))
        reg.register(AgentEntry("srv-b", "/keys/b", sock_b))
        assert reg.reattach("srv-a").socket == sock_a
        assert reg.reattach("srv-b").socket == sock_b
        assert reg.get("srv-a").key == "/keys/a"
        assert reg.get("srv-b").key == "/keys/b"


class TestPrune:
    def test_prune_drops_only_dead(self, tmp_path):
        live = _live_socket(tmp_path, "live.sock")
        # dead socket file exists but ssh-add reports 2 for its path... we need
        # per-call rc. Simpler: dead entry points at a missing socket.
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-add": (0, "", "")}))
        reg.register(AgentEntry("live", "/k", live))
        reg.register(AgentEntry("dead", "/k", str(tmp_path / "missing.sock")))
        removed = reg.prune_dead()
        assert removed == ["dead"]
        assert set(reg.entries()) == {"live"}


class TestSpawnAndLoad:
    def test_spawn_parses_pid_and_registers(self, tmp_path):
        out = (
            "SSH_AUTH_SOCK=/tmp/x.sock; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=9931; export SSH_AGENT_PID;\n"
        )
        runner = FakeRunner({"ssh-agent": (0, out, "")})
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)
        entry = reg.spawn_agent("sunflowers", "/keys/sun")
        assert entry.pid == 9931
        assert entry.socket.endswith("agent-sunflowers.sock")
        assert reg.get("sunflowers") == entry
        # the bind path (-a <socket>) was passed
        argv, _ = runner.calls[0]
        assert argv[0] == "ssh-agent" and "-a" in argv

    def test_spawn_failure_raises(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner({"ssh-agent": (1, "", "boom")}))
        with pytest.raises(SshRegistryError, match="spawn failed"):
            reg.spawn_agent("t", "/k")

    def test_load_key_passes_path_only(self, tmp_path):
        runner = FakeRunner({"ssh-add": (0, "", "")})
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)
        reg.register(AgentEntry("t", "/keys/priv", str(tmp_path / "t.sock")))
        assert reg.load_key("t") is True
        argv, env = runner.calls[-1]
        assert argv == ["ssh-add", "/keys/priv"]  # a PATH, never key bytes
        assert env["SSH_AUTH_SOCK"] == str(tmp_path / "t.sock")

    def test_load_key_unknown_target_raises(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run", runner=FakeRunner())
        with pytest.raises(SshRegistryError, match="not registered"):
            reg.load_key("nope")


class TestSshConfigIdentity:
    def test_resolves_host_alias_identityfile(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text(
            "Host sunflowers\n"
            "    HostName sunflowers.example.com\n"
            "    IdentityFile ~/.ssh/sunflowers_ed25519\n"
            "\nHost other\n"
            "    IdentityFile /keys/other\n"
        )
        got = ssh_config_identity("sunflowers", cfg)
        assert got is not None and got.endswith("/.ssh/sunflowers_ed25519")
        assert ssh_config_identity("other", cfg) == "/keys/other"

    def test_multi_pattern_host_line(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host prod staging\n    IdentityFile /keys/shared\n")
        assert ssh_config_identity("staging", cfg) == "/keys/shared"

    def test_unknown_alias_returns_none(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host a\n    IdentityFile /k\n")
        assert ssh_config_identity("missing", cfg) is None

    def test_missing_config_returns_none(self, tmp_path):
        assert ssh_config_identity("x", tmp_path / "nope") is None


class TestValuesNeverExposed:
    def test_entry_holds_only_locators(self):
        entry = AgentEntry("t", "/keys/priv", "/run/t.sock", 42)
        fields = set(vars(entry))
        assert fields == {"target", "key", "socket", "pid"}
        # 'key' is a path/alias locator — no field carries key material
        assert not any(f in ("secret", "private_key", "key_material") for f in fields)


class TestSshConfigHasHost:
    """1.2 primitive: does ssh_config declare a Host stanza for the alias?"""

    def test_present_single_pattern(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host sunflowers\n    IdentityFile /keys/sun\n")
        assert ssh_config_has_host("sunflowers", cfg) is True

    def test_present_among_multiple_patterns(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host staging prod shared\n    IdentityFile /k\n")
        assert ssh_config_has_host("prod", cfg) is True

    def test_absent_host(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host other\n    IdentityFile /keys/other\n")
        assert ssh_config_has_host("sunflowers", cfg) is False

    def test_missing_config_is_false(self, tmp_path):
        assert ssh_config_has_host("anything", tmp_path / "nope") is False

    def test_host_without_identityfile_still_counts(self, tmp_path):
        # A Host can select its key by other means; existence is what 1.2 checks.
        cfg = tmp_path / "config"
        cfg.write_text("Host keyless\n    HostName example.com\n")
        assert ssh_config_has_host("keyless", cfg) is True

    def test_comments_and_blanks_ignored(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("# a comment\n\nHost real\n    User git\n")
        assert ssh_config_has_host("real", cfg) is True
        assert ssh_config_has_host("comment", cfg) is False
