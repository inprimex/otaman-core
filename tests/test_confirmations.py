"""Tests for the confirmation-ledger primitive (bus-test-isolation 1.3)."""

from __future__ import annotations

import stat
import sys

import pytest

from otaman_core.confirmations import (
    LedgerError,
    LedgerRecord,
    append_confirmation,
    default_ledger_path,
    hash_message,
    verify_confirmation,
)


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "otaman" / "confirmations.log"


def _append(ledger, msg_id="m1", content="halt now", command="emergency-halt", agent="cli-agent"):
    return append_confirmation(
        message_id=msg_id,
        content_hash=hash_message(content),
        command=command,
        agent=agent,
        timestamp="2026-08-16T00:00:00+00:00",
        path=ledger,
    )


class TestAppendVerify:
    def test_append_returns_record(self, ledger):
        rec = _append(ledger)
        assert isinstance(rec, LedgerRecord)
        assert rec.message_id == "m1"
        assert rec.command == "emergency-halt"
        assert rec.agent == "cli-agent"
        assert rec.content_hash == hash_message("halt now")

    def test_append_then_verify_true(self, ledger):
        _append(ledger)
        assert verify_confirmation(
            message_id="m1", content_hash=hash_message("halt now"), path=ledger
        )

    def test_verify_wrong_content_false(self, ledger):
        _append(ledger)
        # same id, tampered content -> hash mismatch -> not verified
        assert not verify_confirmation(
            message_id="m1", content_hash=hash_message("halt LATER"), path=ledger
        )

    def test_verify_unknown_id_false(self, ledger):
        _append(ledger)
        assert not verify_confirmation(
            message_id="m2", content_hash=hash_message("halt now"), path=ledger
        )

    def test_missing_ledger_verifies_false(self, ledger):
        # fail closed: no ledger at all -> nothing is verified
        assert not verify_confirmation(message_id="m1", content_hash="x", path=ledger)

    def test_multiple_records(self, ledger):
        _append(ledger, msg_id="a", content="AA")
        _append(ledger, msg_id="b", content="BB")
        assert verify_confirmation(message_id="a", content_hash=hash_message("AA"), path=ledger)
        assert verify_confirmation(message_id="b", content_hash=hash_message("BB"), path=ledger)
        assert not verify_confirmation(message_id="a", content_hash=hash_message("BB"), path=ledger)

    def test_default_timestamp_is_iso_utc(self, ledger):
        rec = append_confirmation(
            message_id="m1",
            content_hash=hash_message("x"),
            command="approve",
            agent="core-agent",
            path=ledger,
        )
        # ISO-8601 with a UTC offset
        assert "T" in rec.timestamp
        assert rec.timestamp.endswith("+00:00")


class TestSecurity:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX mode bits — Windows uses ACLs, not 0600 octal modes",
    )
    def test_ledger_file_is_0600(self, ledger):
        _append(ledger)
        mode = stat.S_IMODE(ledger.stat().st_mode)
        assert mode == 0o600, f"ledger mode {oct(mode)} != 0600"

    @pytest.mark.parametrize("field", ["message_id", "content_hash", "command", "agent"])
    def test_reserved_separator_rejected(self, ledger, field):
        kwargs = {
            "message_id": "m1",
            "content_hash": hash_message("x"),
            "command": "approve",
            "agent": "core-agent",
            "path": ledger,
        }
        kwargs[field] = "bad\tvalue"
        with pytest.raises(LedgerError, match="separator"):
            append_confirmation(**kwargs)


class TestHelpers:
    def test_hash_message_stable_and_sensitive(self):
        assert hash_message("abc") == hash_message("abc")
        assert hash_message("abc") != hash_message("abd")

    def test_default_ledger_path_under_home_otaman(self):
        p = default_ledger_path()
        assert p.name == "confirmations.log"
        assert p.parent.name == ".otaman"
