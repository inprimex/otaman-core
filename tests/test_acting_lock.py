"""Tests for otaman_core.acting_lock — identity-lock primitive (single-acting-session-guard 0.1)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from otaman_core.acting_lock import (
    LOCK_MODES,
    ActingLockError,
    ActingLockHeld,
    acquire,
    clear_preempt_marker,
    holder_info,
    holder_pid,
    lock_key,
    lock_path,
    locks_dir,
    probe,
    read_preempt_marker,
    write_preempt_marker,
)

URI = "otaman://acme/prog/core-agent"
KEY = "acme--prog--core-agent"

_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="flock/fcntl is POSIX-only")


class TestLockKeyAndPath:
    def test_lock_key_from_full_uri(self):
        assert lock_key(URI) == KEY

    def test_lock_key_program_scoped(self):
        # same agent, different program -> different key
        assert lock_key("otaman://acme/other/core-agent") == "acme--other--core-agent"

    def test_lock_key_requires_full_uri(self):
        for bad in ("core-agent", "core-agent@prog", "", "http://x/y/z"):
            with pytest.raises(ActingLockError):
                lock_key(bad)

    def test_lock_key_bad_slug_raises(self):
        with pytest.raises(ActingLockError):
            lock_key("otaman://acme/prog/Bad Agent")

    def test_lock_path_xdg(self, tmp_path):
        assert lock_path(URI, runtime_dir=tmp_path) == tmp_path / "otaman" / f"{KEY}.lock"

    def test_lock_path_home_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert lock_path(URI, home=tmp_path) == tmp_path / ".otaman" / "locks" / f"{KEY}.lock"

    def test_lock_path_accepts_precomputed_key(self, tmp_path):
        assert lock_path(KEY, runtime_dir=tmp_path) == tmp_path / "otaman" / f"{KEY}.lock"

    def test_locks_dir_prefers_xdg(self, tmp_path):
        assert locks_dir(runtime_dir=tmp_path) == tmp_path / "otaman"


@_posix_only
class TestAcquireRelease:
    def test_acquire_returns_held_lock_and_writes_info(self, tmp_path):
        lock = acquire(
            URI, mode="interactive", tmux_session="prog:core-agent", runtime_dir=tmp_path
        )
        assert lock.key == KEY
        assert lock.path == tmp_path / "otaman" / f"{KEY}.lock"
        info = holder_info(URI, runtime_dir=tmp_path)
        assert info["pid"] == os.getpid()
        assert info["mode"] == "interactive"
        assert info["tmux_session"] == "prog:core-agent"
        assert info["started_at"]
        lock.release()

    def test_second_acquire_raises_held_with_holder(self, tmp_path):
        first = acquire(URI, mode="background", tmux_session="s1", runtime_dir=tmp_path)
        try:
            with pytest.raises(ActingLockHeld) as ei:
                acquire(URI, mode="interactive", runtime_dir=tmp_path)
            assert ei.value.key == KEY
            assert ei.value.holder["pid"] == os.getpid()
            assert ei.value.holder["tmux_session"] == "s1"
        finally:
            first.release()

    def test_release_frees_the_lock(self, tmp_path):
        acquire(URI, mode="background", runtime_dir=tmp_path).release()
        # a fresh acquire now succeeds
        acquire(URI, mode="interactive", runtime_dir=tmp_path).release()

    def test_context_manager_releases(self, tmp_path):
        with acquire(URI, mode="interactive", runtime_dir=tmp_path):
            with pytest.raises(ActingLockHeld):
                acquire(URI, mode="background", runtime_dir=tmp_path)
        # released on exit -> reacquire succeeds
        acquire(URI, mode="background", runtime_dir=tmp_path).release()

    def test_release_clears_info_sidecar(self, tmp_path):
        lock = acquire(URI, mode="interactive", runtime_dir=tmp_path)
        assert lock.info_path.is_file()
        lock.release()
        assert not lock.info_path.exists()

    def test_release_idempotent(self, tmp_path):
        lock = acquire(URI, mode="interactive", runtime_dir=tmp_path)
        lock.release()
        lock.release()  # no error

    def test_invalid_mode_raises(self, tmp_path):
        with pytest.raises(ActingLockError, match="mode"):
            acquire(URI, mode="turbo", runtime_dir=tmp_path)

    def test_holder_pid(self, tmp_path):
        lock = acquire(URI, mode="background", runtime_dir=tmp_path)
        try:
            assert holder_pid(URI, runtime_dir=tmp_path) == os.getpid()
        finally:
            lock.release()


@_posix_only
class TestProbe:
    def test_probe_none_when_no_lock(self, tmp_path):
        assert probe(URI, runtime_dir=tmp_path) is None

    def test_probe_returns_holder_when_held(self, tmp_path):
        lock = acquire(URI, mode="interactive", tmux_session="s9", runtime_dir=tmp_path)
        try:
            info = probe(URI, runtime_dir=tmp_path)
            assert info and info["tmux_session"] == "s9"
        finally:
            lock.release()

    def test_probe_none_after_release(self, tmp_path):
        acquire(URI, mode="interactive", runtime_dir=tmp_path).release()
        assert probe(URI, runtime_dir=tmp_path) is None


class TestPreemptMarker:
    def test_write_read_clear(self, tmp_path):
        write_preempt_marker(URI, pid=4242, mode="interactive", runtime_dir=tmp_path)
        marker = read_preempt_marker(URI, runtime_dir=tmp_path)
        assert marker["pid"] == 4242 and marker["mode"] == "interactive" and marker["timestamp"]
        clear_preempt_marker(URI, runtime_dir=tmp_path)
        assert read_preempt_marker(URI, runtime_dir=tmp_path) is None

    def test_read_absent_is_none(self, tmp_path):
        assert read_preempt_marker(URI, runtime_dir=tmp_path) is None

    def test_clear_absent_is_noop(self, tmp_path):
        clear_preempt_marker(URI, runtime_dir=tmp_path)  # no error

    def test_invalid_mode_raises(self, tmp_path):
        with pytest.raises(ActingLockError, match="mode"):
            write_preempt_marker(URI, pid=1, mode="nope", runtime_dir=tmp_path)


# ---------------------------------------------------------------------------
# The headline guarantee: a crashed (kill -9) holder auto-releases via the kernel.

_CHILD = """
import os, time
from otaman_core.acting_lock import acquire
lock = acquire({uri!r}, mode="background", runtime_dir={rt!r})
open({ready!r}, "w").close()
time.sleep(60)
""".strip()


@_posix_only
def test_kill9_holder_auto_releases(tmp_path):
    ready = tmp_path / "ready"
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(uri=URI, rt=str(tmp_path), ready=str(ready))]
    )
    try:
        # wait for the child to acquire + signal
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "child never acquired the lock"
        # the lock is genuinely held by the (live) child
        assert probe(URI, runtime_dir=tmp_path) is not None
        # SIGKILL — no chance to release cooperatively
        child.kill()
        child.wait(timeout=5)
        # kernel released the flock on process death: successor acquires immediately
        lock = acquire(URI, mode="interactive", runtime_dir=tmp_path)
        assert lock.key == KEY
        lock.release()
    finally:
        if child.poll() is None:
            child.kill()


def test_lock_modes_constant():
    assert LOCK_MODES == ("interactive", "background")
