"""The registry access contract — one chokepoint over the decision registers (rac 1.1).

The outcomes register is the platform's differentiator: a git-versioned DECISION record
— who approved what, against which spec. Today it is read/written directly from twelve
call sites in otaman-cli, so no adapter can sit over it and a future backend could land
``UPDATE … SET status`` and silently delete the history. This module is the single
chokepoint (design D2/D3/D4): every reader and writer goes through it, and the write
contract is structural, not incidental.

The write contract (D3), enforced here:

- **append-only, never in-place.** The only write paths are :func:`apply_transition`
  (which ALWAYS appends a transition audit entry) and :func:`create_record` (the one
  path that invents a record). There is no "set status" API, so a status change without
  a recorded transition is impossible by construction — the transitionless-write-fails
  clause the contract suite checks.
- **authorization in the write path.** An authority-bearing action
  (:data:`APPROVAL_REQUIRED_ACTIONS` — the fund/choose decisions, D2) carries an
  approval record; core enforces its PRESENCE and SHAPE ({by, at, via, spec}, ``by`` a
  resolved roster human, mirroring ``lifecycle.record_transition``), while the surface
  OBTAINS it (roster/hat/HITL — core grows no roster reader, Q3).

The file backend is ruamel round-trip (comment + key-order preserving) so writes are
byte-equivalent over the human-annotated registers (spec-agent ruling A — pyyaml would
strip the annotations). The transition shape matches canon Appendix A.5; full schema
validation (Appendix A, the ``remainder`` field, abolishing Done-Partial) is rac 1.3,
and the backend Protocol is extracted in rac 2.2 — 1.1 is the concrete chokepoint.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

#: Where the records list lives in a register file (the outcomes register uses this).
DEFAULT_RECORDS_KEY = "outcomes"

#: Actions that MUST carry an approval record (D2/D3 — the fund/choose decisions that
#: are the registry's HITL points). Grounded in the verb set cli named; other
#: transitions (status moves, field updates, estimate requests) record an actor but do
#: not require a separate approval. Confirm/extend with cli+spec-agent as the verb split
#: (rac 2.1) lands.
APPROVAL_REQUIRED_ACTIONS: frozenset[str] = frozenset({"accept-cost", "choose", "reject-cost"})

#: Valid ``approval.via`` values — how the authority was established.
APPROVAL_VIA: tuple[str, ...] = ("hitl", "roster-role", "hat")


class RegistryAccessError(ValueError):
    """A contract violation — a transitionless write, or a missing/malformed approval."""


def _yaml() -> YAML:
    """A round-trip YAML configured like the current writer (byte-equivalence)."""
    y = YAML()  # round-trip mode: preserves comments and key order
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


@dataclass
class Register:
    """A loaded register: the parsed (comment-preserving) data + its records key.

    Mutable by design — :func:`apply_transition` / :func:`create_record` mutate it in
    place through the contract, and :func:`save_register` writes it back byte-equivalent.
    ``data`` is the round-trip structure; do not mutate it directly (that would bypass
    the append-only contract) — go through the functions.
    """

    data: Any
    records_key: str = DEFAULT_RECORDS_KEY
    #: True for a fast (lossy, pyyaml) display read — it has dropped comments/quotes, so
    #: :func:`save_register` refuses it (writing it would corrupt the human annotations).
    read_only: bool = False

    def records(self) -> list[Any]:
        recs = self.data.get(self.records_key) if isinstance(self.data, Mapping) else None
        return list(recs) if isinstance(recs, Sequence) else []


def load_register(
    path: Path, *, records_key: str = DEFAULT_RECORDS_KEY, missing_ok: bool = False
) -> Register:
    """Load a register file round-trip (comments + key order preserved).

    ``missing_ok=True`` returns an empty register when *path* does not exist — the
    create-fresh case (a program whose register is created by its first write), which
    the reader this replaces handled by returning ``{}`` (cli rac-1.2 finding 1). The
    default raises ``FileNotFoundError`` so a typo'd path is not silently an empty
    register under a verb that reads.
    """
    if missing_ok and not path.exists():
        return Register(data={records_key: []}, records_key=records_key)
    with open(path, encoding="utf-8") as f:
        data = _yaml().load(f)
    return Register(data=data if data is not None else {records_key: []}, records_key=records_key)


def read_register_fast(
    path: Path, *, records_key: str = DEFAULT_RECORDS_KEY, missing_ok: bool = True
) -> Register:
    """A FAST, lossy, read-only load for display paths (cli rac-1.2 finding 5).

    The round-trip :func:`load_register` is ~700ms on the live register (ruamel preserves
    every comment), too slow for a console frame that reads it several times. This uses
    the C-accelerated safe loader instead (~0.1ms) and marks the result
    :attr:`Register.read_only` — it has dropped comments and quote styles, so it is for
    READING only; :func:`save_register` refuses it. Use :func:`load_register` for any
    path that writes.
    """
    import yaml

    if missing_ok and not path.exists():
        return Register(data={records_key: []}, records_key=records_key, read_only=True)
    loader = getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f, Loader=loader)
    return Register(
        data=data if isinstance(data, dict) else {records_key: []},
        records_key=records_key,
        read_only=True,
    )


def save_register(register: Register, path: Path) -> None:
    """Write *register* back byte-equivalent (round-trip serializer).

    Refuses a :attr:`Register.read_only` register — one from :func:`read_register_fast`
    has already lost its comments and quote styles, so writing it would silently corrupt
    the human annotations (cli rac-1.2 finding 5).
    """
    if register.read_only:
        raise RegistryAccessError(
            "cannot save a fast/display register (read_register_fast is lossy); "
            "load_register for a path that writes"
        )
    with open(path, "w", encoding="utf-8") as f:
        _yaml().dump(register.data, f)


def dumps(register: Register) -> str:
    """Serialize *register* to a string (round-trip) — for byte-equivalence checks."""
    buf = io.StringIO()
    _yaml().dump(register.data, buf)
    return buf.getvalue()


def _raw_records(register: Register) -> Any:
    if not isinstance(register.data, Mapping) or register.records_key not in register.data:
        raise RegistryAccessError(f"register has no {register.records_key!r} list")
    return register.data[register.records_key]


def get(register: Register, outcome_id: str) -> Any | None:
    """The record with ``id == outcome_id``, or ``None``."""
    for rec in register.records():
        if isinstance(rec, Mapping) and rec.get("id") == outcome_id:
            return rec
    return None


def create_record(register: Register, record: Mapping[str, Any]) -> None:
    """Append a new record (the one path that invents a record).

    Refuses a record with no ``id`` or a duplicate ``id`` — creation is where identity
    is established, so it cannot be blank or collide. (Full Appendix A validation is
    rac 1.3.)
    """
    rid = record.get("id")
    if not isinstance(rid, str) or not rid:
        raise RegistryAccessError("create_record: a record must carry a non-empty id")
    if get(register, rid) is not None:
        raise RegistryAccessError(f"create_record: id {rid!r} already exists")
    _raw_records(register).append(dict(record))


def _validate_approval(approval: Any, where: str) -> None:
    if not isinstance(approval, Mapping):
        raise RegistryAccessError(f"{where}: approval must be a mapping {{by, at, via, spec}}")
    by = approval.get("by")
    if not isinstance(by, str) or not by.strip():
        raise RegistryAccessError(f"{where}: approval.by must be a resolved roster human")
    if approval.get("via") not in APPROVAL_VIA:
        raise RegistryAccessError(f"{where}: approval.via must be one of {', '.join(APPROVAL_VIA)}")
    for field in ("at", "spec"):
        if not isinstance(approval.get(field), str) or not approval[field].strip():
            raise RegistryAccessError(f"{where}: approval.{field} is required")
    # ``hat`` is optional (A.5) — a structured record of WHICH hat authorized, so D2's
    # "choose (cto) and accept-cost (ceo) as separate entries with their hats" is
    # queryable rather than read off a prose note. When present it must name a hat.
    hat = approval.get("hat")
    if hat is not None and (not isinstance(hat, str) or not hat.strip()):
        raise RegistryAccessError(f"{where}: approval.hat, when present, must be non-empty")


def apply_transition(
    register: Register,
    outcome_id: str,
    *,
    action: str,
    by: str,
    at: str,
    to_status: str | None = None,
    fields: Mapping[str, Any] | None = None,
    approval: Mapping[str, Any] | None = None,
    note: str | None = None,
) -> None:
    """Apply an append-only transition to *outcome_id* — the only status/field write path.

    ALWAYS appends a transition audit entry (``at``/``by``/``action`` + ``from``/``to``
    for a status move, + a ``changes`` list of ``{field, old?, new}`` for every changed
    field, + ``note``), then applies the change to the record in place. Because this is
    the sole write path and it always records the transition, a transitionless status
    write cannot happen (the contract-suite clause).

    The ``changes`` list is the pinned A.5 shape (spec d439a5c; cli's measured shape,
    ``extra="forbid"`` on its side): one entry per field, carrying ``old`` only when the
    field had a previous value — so ``accept-cost`` (which sets several fields at once)
    gets a complete audit, and a first-ever ``choose`` no longer reads as "the previous
    choice was null". It supersedes the flat ``field``/``old``/``new`` trio that could
    only carry one field; historical rows keep the flat trio and readers accept both.

    ``to_status=None`` means NO status change (request-estimate and choose set fields
    only). ``fields`` may set multiple fields in one transition. An action in
    :data:`APPROVAL_REQUIRED_ACTIONS` MUST carry a valid ``approval`` (presence + shape
    enforced here); the caller obtains it. ``by`` is the acting identity; approval's
    ``by`` is the authorizing roster human.

    Call it again on the same register and ``save_register`` once to compose multiple
    transitions (founder-mode's choose+fund records BOTH, D2) — each call appends its
    own audit entry; they are never collapsed.
    """
    record = get(register, outcome_id)
    if record is None:
        raise RegistryAccessError(f"no record with id {outcome_id!r}")
    if action in APPROVAL_REQUIRED_ACTIONS:
        _validate_approval(approval, f"apply_transition({action})")

    entry: dict[str, Any] = {"at": at, "by": by, "action": action}
    changed = dict(fields) if fields else {}
    # one transition carries a status move and/or field changes; record old/new so the
    # audit is reconstructable (choose records the previous choice, Appendix A.5).
    if to_status is not None:
        entry["from"] = record.get("status")
        entry["to"] = to_status
    if changed:
        # the A.5 ``changes`` list: one entry per changed field, carrying ``old`` only
        # when the field had a previous value (``"old" in ch`` then means what it says —
        # not "the previous value was null"; cli rac-1.2 finding 3, per entry).
        change_entries: list[dict[str, Any]] = []
        for fname, fval in changed.items():
            ch: dict[str, Any] = {"field": fname}
            if fname in record:
                ch["old"] = record[fname]
            ch["new"] = fval
            change_entries.append(ch)
        entry["changes"] = change_entries
    if note:
        entry["note"] = note
    if approval is not None:
        entry["approval"] = dict(approval)

    record.setdefault("transitions", [])
    record["transitions"].append(entry)
    if to_status is not None:
        record["status"] = to_status
    for fname, fval in changed.items():
        record[fname] = fval


#: The outcomes-registry schema (rac 1.3), shipped in the package.
_OUTCOMES_SCHEMA_PATH = Path(__file__).parent / "schemas" / "outcomes-registry-schema.yaml"

#: The abolished status (rac 1.3 / spec-agent A.4 ruling) — flagged with a migration hint.
_ABOLISHED_STATUS = "Done-Partial"


def validate_register(register: Register, *, schema_path: Path | None = None) -> list[str]:
    """Report canon violations in *register* — a REPORTING tool, never a load gate.

    Validates each record against the outcomes-registry schema (rac 1.3): the A.4 status
    enum (``Done-Partial`` abolished), ``remainder`` as a string, the identity +
    statement shape. Returns ``["<id or index>: <message>", …]`` — EMPTY when clean. It
    does NOT raise and callers do NOT gate loading on it: the seven legacy Done-Partial
    rows stay readable until cofounder's 1.4 migration converts them to Done + remainder
    (flag-don't-break, spec-agent-confirmed). Historical transitions are validated
    leniently (additionalProperties) — the materialized spec is corpus too, and a
    reader must not choke on an old entry's shape.

    A ``Done-Partial`` status gets a dedicated migration hint on top of the generic
    enum error, so the surface says what to do, not just what is wrong.
    """
    import yaml

    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a core dep
        return []
    path = schema_path or _OUTCOMES_SCHEMA_PATH
    with open(path, encoding="utf-8") as f:
        item_schema = yaml.safe_load(f)["definitions"]["outcome"]
    validator = jsonschema.Draft7Validator(item_schema)

    out: list[str] = []
    for i, rec in enumerate(register.records()):
        if not isinstance(rec, Mapping):
            out.append(f"[{i}]: record is not a mapping")
            continue
        rid = rec.get("id") if isinstance(rec.get("id"), str) else f"[{i}]"
        if rec.get("status") == _ABOLISHED_STATUS:
            out.append(
                f"{rid}: status {_ABOLISHED_STATUS!r} is abolished — migrate to Done with a "
                "required remainder: naming the cut scope (rac 1.3 / cofounder 1.4)"
            )
        for err in sorted(validator.iter_errors(rec), key=lambda e: list(e.path)):
            field = ".".join(str(p) for p in err.path) or "(record)"
            out.append(f"{rid}: {field}: {err.message}")
    return out


def run_contract_suite(
    *,
    load,
    save,
    sample_path: Path,
    tmp_path: Path,
) -> list[str]:
    """Run the backend-agnostic contract suite; return a list of FAILURES (empty = pass).

    The suite every registry backend must pass (rac 2.2 runs it against the Protocol;
    1.1 runs it against this file backend). It asserts the contract CLAUSES, not an
    implementation: (1) round-trip is byte-equivalent (comments survive); (2) a status
    write goes through a transition — after a transition the record's transitions grew by
    exactly one and the status moved together (no transitionless write); (3) an
    approval-required action with no approval fails. *load*/*save* are the backend's
    functions; *sample_path* is a register fixture WITH comments.
    """
    failures: list[str] = []
    original = sample_path.read_text(encoding="utf-8")

    # (1) byte-equivalent round-trip
    reg = load(sample_path)
    out = tmp_path / "roundtrip.yaml"
    save(reg, out)
    if out.read_text(encoding="utf-8") != original:
        failures.append("round-trip is not byte-equivalent (comments or key order lost)")

    # (2) a status write is always a recorded transition
    reg = load(sample_path)
    rid = reg.records()[0]["id"]
    before = len((get(reg, rid) or {}).get("transitions", []))
    apply_transition(
        reg, rid, action="promote", by="agent", at="2026-01-01T00:00:00Z", to_status="Done"
    )
    rec = get(reg, rid) or {}
    if rec.get("status") != "Done":
        failures.append("status change did not apply")
    if len(rec.get("transitions", [])) != before + 1:
        failures.append("status write did not append exactly one transition (transitionless write)")

    # (3) an approval-required action with no approval fails
    reg = load(sample_path)
    rid = reg.records()[0]["id"]
    try:
        apply_transition(reg, rid, action="accept-cost", by="agent", at="2026-01-01T00:00:00Z")
        failures.append("accept-cost without approval was permitted (HITL not enforced)")
    except RegistryAccessError:
        pass
    return failures


__all__ = [
    "APPROVAL_REQUIRED_ACTIONS",
    "APPROVAL_VIA",
    "DEFAULT_RECORDS_KEY",
    "Register",
    "RegistryAccessError",
    "apply_transition",
    "create_record",
    "dumps",
    "get",
    "load_register",
    "read_register_fast",
    "run_contract_suite",
    "save_register",
    "validate_register",
]
