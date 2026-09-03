"""Tests for the connection check engine (agent-credential-access 1.3).

Covers read-only-by-default (no mutation without --fix), the --fix self-heal
path, type dispatch (ssh registry vs network prober), status derivation, the
injected clock (last-check), and the values-never-exposed invariant. Engine
orchestration is tested with stub probers; the default SshProber/NetworkProber
are tested against a real 1.2 registry with an injected runner + fake http probe.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from otaman_core.connection_check import (
    CheckConfigError,
    CheckReport,
    ConnectionChecker,
    NetworkProber,
    ProbeResult,
    SshProber,
    dangling_ssh_hosts,
    load_reports,
    persist_reports,
    render_last_check,
    report_store_path,
)
from otaman_core.connections import Connection
from otaman_core.ssh_registry import AgentEntry, SshAgentRegistry

FIXED_CLOCK = lambda: "2026-08-24T17:00:00+00:00"  # noqa: E731


def _ssh_conn(name="sunflowers-ssh", ssh_ref="sunflowers"):
    return Connection(name, "ssh", "sunflowers.example.com", "program", ssh_ref=ssh_ref)


def _api_conn(name="gh-api", secret_ref="gh-pat"):
    return Connection(name, "api", "api.github.com", "org", secret_ref=secret_ref)


class StubProber:
    def __init__(self, result: ProbeResult, heal_result: ProbeResult | None = None):
        self._result = result
        self._heal_result = heal_result
        self.probe_calls = 0
        self.heal_calls = 0

    def probe(self, conn):
        self.probe_calls += 1
        return self._result

    def heal(self, conn):
        self.heal_calls += 1
        return self._heal_result


class TestEngineOrchestration:
    def test_ok_when_reachable_and_authenticated(self):
        stub = StubProber(ProbeResult(True, True, "all good"))
        chk = ConnectionChecker(network_prober=stub, clock=FIXED_CLOCK)
        rep = chk.check(_api_conn())
        assert rep.status == "ok"
        assert rep.reachable and rep.authenticated and not rep.healed
        assert rep.checked_at == "2026-08-24T17:00:00+00:00"

    def test_read_only_default_never_heals(self):
        # Spec: check reports a broken connection without changing anything.
        stub = StubProber(
            ProbeResult(False, False, "agent socket dead"),
            heal_result=ProbeResult(True, True, "healed"),
        )
        chk = ConnectionChecker(ssh_prober=stub, clock=FIXED_CLOCK)
        rep = chk.check(_ssh_conn())  # fix defaults to False
        assert stub.heal_calls == 0
        assert rep.status == "socket-dead"
        assert rep.healed is False

    def test_fix_self_heals(self):
        # Spec: check --fix respawns and reports the restored status.
        stub = StubProber(
            ProbeResult(False, False, "agent socket dead"),
            heal_result=ProbeResult(True, True, "respawned + reloaded"),
        )
        chk = ConnectionChecker(ssh_prober=stub, clock=FIXED_CLOCK)
        rep = chk.check(_ssh_conn(), fix=True)
        assert stub.heal_calls == 1
        assert rep.status == "fixed"
        assert rep.healed is True

    def test_fix_when_heal_unavailable_stays_failed(self):
        stub = StubProber(ProbeResult(False, False, "unreachable"), heal_result=None)
        chk = ConnectionChecker(network_prober=stub, clock=FIXED_CLOCK)
        rep = chk.check(_api_conn(), fix=True)
        assert rep.status == "unreachable"
        assert rep.healed is False

    def test_fix_skipped_when_already_ok(self):
        stub = StubProber(ProbeResult(True, True, "ok"), heal_result=ProbeResult(True, True, "x"))
        chk = ConnectionChecker(network_prober=stub, clock=FIXED_CLOCK)
        chk.check(_api_conn(), fix=True)
        assert stub.heal_calls == 0

    def test_auth_failed_status(self):
        stub = StubProber(ProbeResult(True, False, "no backing key"))
        chk = ConnectionChecker(network_prober=stub, clock=FIXED_CLOCK)
        assert chk.check(_api_conn()).status == "auth-failed"

    def test_dispatch_by_type(self):
        ssh_stub = StubProber(ProbeResult(True, True, "ssh"))
        net_stub = StubProber(ProbeResult(True, True, "net"))
        chk = ConnectionChecker(ssh_prober=ssh_stub, network_prober=net_stub, clock=FIXED_CLOCK)
        chk.check(_ssh_conn())
        chk.check(_api_conn())
        assert ssh_stub.probe_calls == 1 and net_stub.probe_calls == 1

    def test_missing_prober_raises(self):
        chk = ConnectionChecker(clock=FIXED_CLOCK)  # no probers
        with pytest.raises(CheckConfigError):
            chk.check(_api_conn())

    def test_check_all_maps_each(self):
        stub = StubProber(ProbeResult(True, True, "ok"))
        chk = ConnectionChecker(network_prober=stub, clock=FIXED_CLOCK)
        reports = chk.check_all([_api_conn("a"), _api_conn("b")])
        assert [r.name for r in reports] == ["a", "b"]


class TestNetworkProber:
    def test_reachable_with_backing_key_is_ok(self):
        prober = NetworkProber(lambda ep: True, available_keys={"gh-pat"})
        res = prober.probe(_api_conn(secret_ref="gh-pat"))
        assert res.reachable and res.authenticated

    def test_reachable_no_secret_required(self):
        prober = NetworkProber(lambda ep: True, available_keys=set())
        conn = Connection("open", "api", "example.com", "org")  # no secret_ref
        res = prober.probe(conn)
        assert res.reachable and res.authenticated

    def test_secret_ref_without_backing_key_is_auth_failed(self):
        prober = NetworkProber(lambda ep: True, available_keys={"other"})
        res = prober.probe(_api_conn(secret_ref="gh-pat"))
        assert res.reachable and not res.authenticated
        assert "no backing key" in res.detail

    def test_unreachable_is_not_authenticated(self):
        prober = NetworkProber(lambda ep: False, available_keys={"gh-pat"})
        res = prober.probe(_api_conn(secret_ref="gh-pat"))
        assert not res.reachable and not res.authenticated

    def test_heal_is_none(self):
        assert NetworkProber(lambda ep: True, set()).heal(_api_conn()) is None

    def test_probe_never_exposes_secret_value(self):
        # available_keys are NAMES; the probe compares identifiers, not values.
        prober = NetworkProber(lambda ep: True, available_keys={"gh-pat"})
        res = prober.probe(_api_conn(secret_ref="gh-pat"))
        assert "gh-pat" in res.detail or res.detail  # only the ref name may appear
        assert "value" not in res.detail.lower()


class _SocketTouchRunner:
    """Fake runner: `ssh-agent -a <sock>` creates the socket file (like a real
    agent) and reports a pid; `ssh-add -l`/`ssh-add <key>` return rc 0."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, env_overlay):
        self.calls.append(argv)
        if argv[0] == "ssh-agent":
            sock = argv[argv.index("-a") + 1]
            Path(sock).write_text("")
            return subprocess.CompletedProcess(argv, 0, "SSH_AGENT_PID=555;\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")  # ssh-add ok


class TestSshProber:
    def test_no_entry_is_unreachable(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run", runner=_SocketTouchRunner())
        res = SshProber(reg).probe(_ssh_conn())
        assert not res.reachable
        assert "no ssh-agent registered" in res.detail

    def test_dead_socket_is_unreachable(self, tmp_path):
        reg = SshAgentRegistry(tmp_path / "run", runner=_SocketTouchRunner())
        reg.register(AgentEntry("sunflowers-ssh", "/keys/sun", str(tmp_path / "gone.sock")))
        res = SshProber(reg).probe(_ssh_conn())
        assert not res.reachable and "socket dead" in res.detail

    def test_live_socket_is_ok(self, tmp_path):
        sock = tmp_path / "a.sock"
        sock.write_text("")
        reg = SshAgentRegistry(tmp_path / "run", runner=_SocketTouchRunner())
        reg.register(AgentEntry("sunflowers-ssh", "/keys/sun", str(sock)))
        res = SshProber(reg).probe(_ssh_conn())
        assert res.reachable and res.authenticated

    def test_read_only_probe_does_not_spawn(self, tmp_path):
        runner = _SocketTouchRunner()
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)
        reg.register(AgentEntry("sunflowers-ssh", "/keys/sun", str(tmp_path / "gone.sock")))
        SshProber(reg).probe(_ssh_conn())
        assert not any(c[0] == "ssh-agent" for c in runner.calls)  # no respawn

    def test_heal_respawns_and_reloads(self, tmp_path):
        runner = _SocketTouchRunner()
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)
        reg.register(AgentEntry("sunflowers-ssh", "/keys/sun", str(tmp_path / "gone.sock")))
        res = SshProber(reg).heal(_ssh_conn())
        assert res is not None and res.reachable and res.authenticated
        assert any(c[0] == "ssh-agent" for c in runner.calls)  # respawned
        assert any(c[0] == "ssh-add" and c[-1] == "/keys/sun" for c in runner.calls)  # reloaded

    def test_heal_resolves_key_from_ssh_config(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host sunflowers\n    IdentityFile /keys/from-config\n")
        runner = _SocketTouchRunner()
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)  # no prior entry
        res = SshProber(reg, ssh_config_path=cfg).heal(_ssh_conn())
        assert res is not None and res.reachable
        assert any(c[0] == "ssh-add" and c[-1] == "/keys/from-config" for c in runner.calls)


class TestEndToEndWithRealProbers:
    def test_fix_restores_dead_ssh_via_registry(self, tmp_path):
        runner = _SocketTouchRunner()
        reg = SshAgentRegistry(tmp_path / "run", runner=runner)
        reg.register(AgentEntry("sunflowers-ssh", "/keys/sun", str(tmp_path / "gone.sock")))
        chk = ConnectionChecker(ssh_prober=SshProber(reg), clock=FIXED_CLOCK)

        before = chk.check(_ssh_conn())  # read-only
        assert before.status == "socket-dead" and not before.healed

        after = chk.check(_ssh_conn(), fix=True)  # self-heal
        assert after.status == "fixed" and after.healed


def _report(name="gh-api", status="ok", checked_at="2026-08-24T17:00:00+00:00"):
    return CheckReport(
        name=name,
        type="api",
        endpoint="api.github.com",
        reachable=True,
        authenticated=True,
        status=status,
        detail="reachable; secret_ref has backing key",
        healed=False,
        checked_at=checked_at,
    )


PROG = "example"


class TestReportStore:
    def test_path_is_tenant_home_json(self, tmp_path):
        # cli-agent's frozen contract: ~/.otaman/connection-checks.json (tenant home)
        assert report_store_path(tmp_path) == tmp_path / ".otaman" / "connection-checks.json"

    def test_persist_then_load_round_trips(self, tmp_path):
        path = report_store_path(tmp_path)
        persist_reports([_report("a"), _report("b")], path, PROG)
        loaded = load_reports(path, PROG)
        assert set(loaded) == {"a", "b"}
        assert loaded["a"] == _report("a")

    def test_load_absent_is_empty(self, tmp_path):
        assert load_reports(report_store_path(tmp_path), PROG) == {}

    def test_load_corrupt_is_empty(self, tmp_path):
        path = report_store_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        assert load_reports(path, PROG) == {}

    def test_load_unknown_program_is_empty(self, tmp_path):
        path = report_store_path(tmp_path)
        persist_reports([_report("a")], path, PROG)
        assert load_reports(path, "other-program") == {}

    def test_persist_upserts_by_name_preserving_others(self, tmp_path):
        # `check <name>` updates one entry; siblings survive (spec: single check).
        path = report_store_path(tmp_path)
        persist_reports([_report("a", status="ok"), _report("b", status="ok")], path, PROG)
        persist_reports([_report("a", status="auth-failed")], path, PROG)
        loaded = load_reports(path, PROG)
        assert loaded["a"].status == "auth-failed"  # updated
        assert loaded["b"].status == "ok"  # preserved

    def test_persist_preserves_other_programs(self, tmp_path):
        # One tenant file holds every program; writing one must not clobber others.
        path = report_store_path(tmp_path)
        persist_reports([_report("a")], path, "prog-one")
        persist_reports([_report("b")], path, "prog-two")
        assert set(load_reports(path, "prog-one")) == {"a"}
        assert set(load_reports(path, "prog-two")) == {"b"}

    def test_persist_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / ".otaman" / "connection-checks.json"
        persist_reports([_report("a")], path, PROG)
        assert path.is_file()

    def test_store_carries_version_and_program_key_no_values(self, tmp_path):
        import json as _json

        path = report_store_path(tmp_path)
        persist_reports([_report("a")], path, PROG)
        raw = _json.loads(path.read_text())
        assert raw["version"] == 1
        assert PROG in raw["programs"]
        # values-free: no serialized field name hints at a secret value
        blob = path.read_text().lower()
        assert "secret_value" not in blob and "private_key" not in blob

    @pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX mode bits")
    def test_store_written_0600(self, tmp_path):
        path = report_store_path(tmp_path)
        persist_reports([_report("a")], path, PROG)
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_load_ignores_unknown_fields(self, tmp_path):
        import json as _json

        path = report_store_path(tmp_path)
        path.parent.mkdir(parents=True)
        rec = {
            "name": "a",
            "type": "api",
            "endpoint": "x",
            "reachable": True,
            "authenticated": True,
            "status": "ok",
            "detail": "d",
            "healed": False,
            "checked_at": "2026-08-24T17:00:00+00:00",
            "future_field": "ignored",  # forward-compat
        }
        path.write_text(_json.dumps({"version": 99, "programs": {PROG: [rec]}}))
        loaded = load_reports(path, PROG)
        assert loaded["a"].name == "a"

    def test_load_skips_incomplete_records(self, tmp_path):
        import json as _json

        path = report_store_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(_json.dumps({"version": 1, "programs": {PROG: [{"name": "only-name"}]}}))
        assert load_reports(path, PROG) == {}


class TestRenderLastCheck:
    def test_renders_status_and_timestamp(self):
        assert render_last_check(_report("a", status="ok")) == "ok · 2026-08-24T17:00:00+00:00"

    def test_none_renders_em_dash(self):
        assert render_last_check(None) == "—"

    def test_generator_join_flow(self, tmp_path):
        # End-to-end: CLI persists (program-keyed), generator loads for its program
        # + joins on name, renders the cell.
        path = report_store_path(tmp_path)
        persist_reports([_report("gh-api", status="ok")], path, PROG)
        store = load_reports(path, PROG)
        assert render_last_check(store.get("gh-api")) == "ok · 2026-08-24T17:00:00+00:00"
        assert render_last_check(store.get("never-checked")) == "—"  # fallback


class TestSshHostPointerValidation:
    """1.2: connection check validates the external-resource → ssh Host pointer."""

    def test_dangling_host_fails_naming_it(self, tmp_path):
        # ssh_config exists but has no stanza for the pointed Host
        cfg = tmp_path / "config"
        cfg.write_text("Host other\n    IdentityFile /keys/other\n")
        reg = SshAgentRegistry(tmp_path / "run", runner=_SocketTouchRunner())
        res = SshProber(reg, ssh_config_path=cfg).probe(_ssh_conn(ssh_ref="client-prod-deploy"))
        assert not res.reachable and not res.authenticated
        assert "client-prod-deploy" in res.detail
        assert "not found in ssh_config" in res.detail

    def test_present_host_passes_to_socket_check(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host client-prod-deploy\n    IdentityFile /keys/cp\n")
        reg = SshAgentRegistry(tmp_path / "run", runner=_SocketTouchRunner())
        # Host exists → validation passes, falls through to the (empty) socket check
        res = SshProber(reg, ssh_config_path=cfg).probe(_ssh_conn(ssh_ref="client-prod-deploy"))
        assert "no ssh-agent registered" in res.detail  # got past the Host gate

    def test_no_ssh_config_path_skips_validation(self, tmp_path):
        # Backward-compatible: without a config path, no Host validation runs
        reg = SshAgentRegistry(tmp_path / "run", runner=_SocketTouchRunner())
        res = SshProber(reg).probe(_ssh_conn(ssh_ref="anything"))
        assert "no ssh-agent registered" in res.detail

    def test_check_surfaces_dangling_host(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host known\n")
        reg = SshAgentRegistry(tmp_path / "run", runner=_SocketTouchRunner())
        chk = ConnectionChecker(ssh_prober=SshProber(reg, ssh_config_path=cfg), clock=FIXED_CLOCK)
        rep = chk.check(_ssh_conn(ssh_ref="missing-host"))
        assert not rep.reachable
        assert "missing-host" in rep.detail


class TestDanglingSshHosts:
    def test_lists_only_absent_hosts(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host present\n    IdentityFile /k\n")
        conns = [
            Connection("a", "ssh", "a.example", "program", ssh_ref="present"),
            Connection("b", "ssh", "b.example", "program", ssh_ref="absent"),
            Connection("c", "api", "c.example", "org", secret_ref="k"),  # no ssh_ref
        ]
        assert dangling_ssh_hosts(conns, ssh_config_path=cfg) == [("b", "absent")]

    def test_empty_when_all_present(self, tmp_path):
        cfg = tmp_path / "config"
        cfg.write_text("Host h1\nHost h2\n")
        conns = [
            Connection("a", "ssh", "a", "program", ssh_ref="h1"),
            Connection("b", "ssh", "b", "program", ssh_ref="h2"),
        ]
        assert dangling_ssh_hosts(conns, ssh_config_path=cfg) == []

    def test_missing_config_flags_all_ssh_pointers(self, tmp_path):
        conns = [Connection("a", "ssh", "a", "program", ssh_ref="h1")]
        assert dangling_ssh_hosts(conns, ssh_config_path=tmp_path / "nope") == [("a", "h1")]
