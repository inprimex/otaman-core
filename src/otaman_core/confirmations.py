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

Provenance flow: the producer hashes the *final rendered on-disk bytes* with
:func:`hash_message`, then appends ``{message_id, content_hash, command,
agent, timestamp}``. The consumer re-hashes the file it reads and calls
:func:`verify_confirmation` with the same ``content_hash`` — byte-exact, no
YAML re-serialization ambiguity.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Privileged message types whose bus files require ledger provenance.
PRIVILEGED_TYPES = frozenset(
    {"human-decision", "spec-change-approved", "spec-change-rejected", "emergency-halt"}
)

#: Gated commands permitted to produce ledger records.
GATED_COMMANDS = frozenset({"approve", "emergency-halt", "hitl-take"})

_FIELD_SEP = "\t"
_N_FIELDS = 5


class LedgerError(RuntimeError):
    """Raised when the ledger cannot be appended — the producer must then refuse
    to write the corresponding bus file (fail closed: no record, no bus file)."""


@dataclass(frozen=True)
class LedgerRecord:
    """One confirmation-ledger entry. ``timestamp`` is ISO-8601 UTC."""

    timestamp: str
    message_id: str
    content_hash: str
    command: str
    agent: str


def default_ledger_path() -> Path:
    """Canonical ledger location: ``~/.otaman/confirmations.log``."""
    return Path.home() / ".otaman" / "confirmations.log"


def hash_message(content: str) -> str:
    """SHA-256 hex digest of a message's exact bytes — the ledger integrity key.

    Producers hash the *final rendered on-disk file content*; consumers hash
    the bytes they read. Same input -> same digest, byte-exact.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def append_confirmation(
    *,
    message_id: str,
    content_hash: str,
    command: str,
    agent: str,
    timestamp: str | None = None,
    path: Path | None = None,
) -> LedgerRecord:
    """Append a confirmation record; return it. Raise :class:`LedgerError` on
    any failure so the caller can refuse to write the bus file (fail closed).

    Args are keyword-only. ``content_hash`` is the producer-computed digest
    (see :func:`hash_message`). ``timestamp`` defaults to now (ISO-8601 UTC).
    """
    for name, value in (
        ("message_id", message_id),
        ("content_hash", content_hash),
        ("command", command),
        ("agent", agent),
    ):
        if _FIELD_SEP in value or "\n" in value:
            raise LedgerError(f"{name} contains a reserved separator: {value!r}")
    record = LedgerRecord(
        timestamp=timestamp or _utc_now_iso(),
        message_id=message_id,
        content_hash=content_hash,
        command=command,
        agent=agent,
    )
    line = (
        _FIELD_SEP.join(
            (record.timestamp, record.message_id, record.content_hash, record.command, record.agent)
        )
        + "\n"
    )
    ledger = path or default_ledger_path()
    try:
        ledger.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # 0600 from the first write so the record is never group/world-readable.
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(ledger, 0o600)
    except OSError as exc:
        raise LedgerError(f"failed to append confirmation to {ledger}: {exc}") from exc
    return record


def verify_confirmation(
    *,
    message_id: str,
    content_hash: str,
    path: Path | None = None,
) -> bool:
    """True iff a ledger record matches this ``(message_id, content_hash)``.

    A missing ledger, or no matching record, returns ``False`` (fail closed).
    """
    ledger = path or default_ledger_path()
    if not ledger.is_file():
        return False
    try:
        with open(ledger, encoding="utf-8") as fh:
            for raw in fh:
                parts = raw.rstrip("\n").split(_FIELD_SEP)
                if len(parts) != _N_FIELDS:
                    continue
                _ts, rec_id, rec_hash, _cmd, _agent = parts
                if rec_id == message_id and rec_hash == content_hash:
                    return True
    except OSError:
        return False
    return False
