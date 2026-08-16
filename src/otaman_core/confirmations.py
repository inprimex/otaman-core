"""Host-local confirmation ledger for privileged bus messages (bus-test-isolation).

The forged-halt vector is a local file write into a bus directory. A test
suite, script, or attacker can *write* a privileged-looking bus file
(``emergency-halt``, ``spec-change-approved``, ...) but cannot forge a
matching ledger entry without the human's file permissions — the same trust
boundary F012's TTY gate established, extended from command-time to read-time.

The TTY-gated producing commands (``otaman approve`` / ``emergency-halt`` /
``hitl take``) call :func:`append_confirmation` after confirmation; consumers
(bridge watcher, ``otaman doctor``) call :func:`verify_confirmation` before
acting on a privileged file.

The ledger lives at ``~/.otaman/confirmations.log`` (mode 0600, dir 0700),
deliberately OUTSIDE every git-tracked bus tree, so ``OTAMAN_TEST_MODE`` tmp
isolation never redirects it and it is never committed.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: Privileged message types whose bus files require ledger provenance.
PRIVILEGED_TYPES = frozenset(
    {"human-decision", "spec-change-approved", "spec-change-rejected", "emergency-halt"}
)

_FIELD_SEP = "\t"


class LedgerError(RuntimeError):
    """Raised when the ledger cannot be appended (e.g. a producer must refuse to
    write the bus file if the ledger append fails)."""


def default_ledger_path() -> Path:
    """Canonical ledger location: ``~/.otaman/confirmations.log``."""
    return Path.home() / ".otaman" / "confirmations.log"


def content_hash(content: str) -> str:
    """SHA-256 hex digest of a message's content — the ledger's integrity key."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def append_confirmation(
    message_id: str,
    content: str,
    timestamp: str,
    *,
    path: Path | None = None,
) -> None:
    """Append a ``{timestamp, message_id, content_hash}`` record to the ledger.

    Creates the ledger dir (0700) and file (0600) if absent. Raises
    :class:`LedgerError` on any I/O failure so the caller can refuse to write
    the corresponding bus file (fail closed).
    """
    ledger = path or default_ledger_path()
    if _FIELD_SEP in message_id or "\n" in message_id:
        raise LedgerError(f"message_id contains a reserved separator: {message_id!r}")
    line = _FIELD_SEP.join((timestamp, message_id, content_hash(content))) + "\n"
    try:
        ledger.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Open with 0600 so the record is never group/world-readable, even on
        # the first write that creates the file.
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(ledger, 0o600)
    except OSError as exc:
        raise LedgerError(f"failed to append confirmation to {ledger}: {exc}") from exc


def verify_confirmation(
    message_id: str,
    content: str,
    *,
    path: Path | None = None,
) -> bool:
    """True if a ledger record matches this ``(message_id, content_hash)``.

    A missing ledger, or no matching record, returns ``False`` (fail closed).
    """
    ledger = path or default_ledger_path()
    if not ledger.is_file():
        return False
    want_hash = content_hash(content)
    try:
        with open(ledger, encoding="utf-8") as fh:
            for raw in fh:
                parts = raw.rstrip("\n").split(_FIELD_SEP)
                if len(parts) != 3:
                    continue
                _ts, rec_id, rec_hash = parts
                if rec_id == message_id and rec_hash == want_hash:
                    return True
    except OSError:
        return False
    return False
