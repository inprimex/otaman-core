"""Tests for the confirmation-ledger primitive (bus-test-isolation 1.3)."""

from __future__ import annotations

import stat

import pytest

from otaman_core.confirmations import (
    LedgerError,
    append_confirmation,
    content_hash,
    default_ledger_path,
    verify_confirmation,
)


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "otaman" / "confirmations.log"


class TestAppendVerify:
    def test_append_then_verify_true(self, ledger):
        append_confirmation("m1", "halt now", "2026-08-16T00:00:00Z", path=ledger)
        assert verify_confirmation("m1", "halt now", path=ledger) is True

    def test_verify_wrong_content_false(self, ledger):
        append_confirmation("m1", "halt now", "2026-08-16T00:00:00Z", path=ledger)
        # same id, tampered content -> hash mismatch -> not verified
        assert verify_confirmation("m1", "halt LATER", path=ledger) is False

    def test_verify_unknown_id_false(self, ledger):
        append_confirmation("m1", "halt now", "2026-08-16T00:00:00Z", path=ledger)
        assert verify_confirmation("m2", "halt now", path=ledger) is False

    def test_missing_ledger_verifies_false(self, ledger):
        # fail closed: no ledger at all -> nothing is verified
        assert verify_confirmation("m1", "x", path=ledger) is False

    def test_multiple_records(self, ledger):
        append_confirmation("a", "AA", "t1", path=ledger)
        append_confirmation("b", "BB", "t2", path=ledger)
        assert verify_confirmation("a", "AA", path=ledger)
        assert verify_confirmation("b", "BB", path=ledger)
        assert not verify_confirmation("a", "BB", path=ledger)


class TestSecurity:
    def test_ledger_file_is_0600(self, ledger):
        append_confirmation("m1", "x", "t", path=ledger)
        mode = stat.S_IMODE(ledger.stat().st_mode)
        assert mode == 0o600, f"ledger mode {oct(mode)} != 0600"

    def test_message_id_with_separator_rejected(self, ledger):
        with pytest.raises(LedgerError, match="separator"):
            append_confirmation("bad\tid", "x", "t", path=ledger)


class TestHelpers:
    def test_content_hash_stable_and_sensitive(self):
        assert content_hash("abc") == content_hash("abc")
        assert content_hash("abc") != content_hash("abd")

    def test_default_ledger_path_under_home_otaman(self):
        p = default_ledger_path()
        assert p.name == "confirmations.log"
        assert p.parent.name == ".otaman"
