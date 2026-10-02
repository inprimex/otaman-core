"""registry-access-contract 1.1 — the chokepoint + the contract suite.

Pins the append-only write contract (no transitionless status write), approval
presence/shape on authority actions, create_record, byte-equivalent round-trip
(comments preserved), and founder-mode twice-save-once (two audit entries).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otaman_core.registry_access import (
    APPROVAL_REQUIRED_ACTIONS,
    RegistryAccessError,
    apply_transition,
    create_record,
    dumps,
    get,
    load_register,
    run_contract_suite,
    save_register,
)

# A register WITH comments + quotes — byte-equivalence is only meaningful on one that
# carries annotations (a comment-free file round-trips under any writer, cli #253/rac).
_SAMPLE = """\
# outcomes register — human-annotated
outcomes:
  - id: JTBD-1-example
    status: Approved  # the live status
    priority: P0
    statement:
      i-want-to: 'see the thing'  # quoted on purpose
    transitions:
      - at: '2026-01-01T00:00:00Z'
        by: roman
        action: promote
        to: Approved
"""


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    p = tmp_path / "outcomes.yaml"
    p.write_text(_SAMPLE, encoding="utf-8")
    return p


def _approval() -> dict:
    return {"by": "roman", "at": "2026-01-02T00:00:00Z", "via": "hitl", "spec": "some-change"}


# --- read + round-trip -------------------------------------------------------


def test_load_and_get(sample):
    reg = load_register(sample)
    assert [r["id"] for r in reg.records()] == ["JTBD-1-example"]
    assert get(reg, "JTBD-1-example")["status"] == "Approved"
    assert get(reg, "nope") is None


def test_round_trip_is_byte_equivalent(sample):
    reg = load_register(sample)
    assert dumps(reg) == sample.read_text(encoding="utf-8")  # comments + quotes survive


# --- append-only write contract ----------------------------------------------


def test_status_write_appends_a_transition(sample):
    reg = load_register(sample)
    before = len(get(reg, "JTBD-1-example")["transitions"])
    apply_transition(
        reg,
        "JTBD-1-example",
        action="promote",
        by="agent",
        at="2026-02-01T00:00:00Z",
        to_status="Done",
    )
    rec = get(reg, "JTBD-1-example")
    assert rec["status"] == "Done"
    assert len(rec["transitions"]) == before + 1
    assert rec["transitions"][-1]["from"] == "Approved" and rec["transitions"][-1]["to"] == "Done"


def test_field_change_records_old_and_new(sample):
    reg = load_register(sample)
    apply_transition(
        reg,
        "JTBD-1-example",
        action="choose",
        by="cto-agent",
        at="2026-02-01T00:00:00Z",
        fields={"chosen-solution": "SOL-9"},
        approval=_approval(),
    )
    t = get(reg, "JTBD-1-example")["transitions"][-1]
    # A.5 changes list: chosen-solution was absent -> old omitted (finding 3), new recorded
    assert t["changes"] == [{"field": "chosen-solution", "new": "SOL-9"}]
    assert "field" not in t  # the flat trio is superseded by the changes list
    assert get(reg, "JTBD-1-example")["chosen-solution"] == "SOL-9"
    assert "to" not in t  # to_status=None -> no status change recorded


def test_unknown_outcome_refuses(sample):
    reg = load_register(sample)
    with pytest.raises(RegistryAccessError):
        apply_transition(reg, "ghost", action="promote", by="a", at="t", to_status="Done")


# --- approval (presence + shape on authority actions) ------------------------


def test_authority_action_requires_approval(sample):
    reg = load_register(sample)
    assert "accept-cost" in APPROVAL_REQUIRED_ACTIONS
    with pytest.raises(RegistryAccessError, match="approval"):
        apply_transition(reg, "JTBD-1-example", action="accept-cost", by="ceo-agent", at="t")


@pytest.mark.parametrize(
    "bad",
    [
        {"by": "", "at": "t", "via": "hitl", "spec": "c"},  # empty by
        {"by": "roman", "at": "t", "via": "telepathy", "spec": "c"},  # bad via
        {"by": "roman", "at": "t", "via": "hitl"},  # missing spec
        "nope",  # not a mapping
    ],
)
def test_malformed_approval_refused(sample, bad):
    reg = load_register(sample)
    with pytest.raises(RegistryAccessError):
        apply_transition(
            reg, "JTBD-1-example", action="accept-cost", by="ceo-agent", at="t", approval=bad
        )


def test_non_authority_action_needs_no_approval(sample):
    reg = load_register(sample)
    apply_transition(
        reg,
        "JTBD-1-example",
        action="request-estimate",
        by="agent",
        at="t",
        fields={"estimate-requested": True},
    )
    assert get(reg, "JTBD-1-example")["estimate-requested"] is True


# --- create ------------------------------------------------------------------


def test_create_record_appends(sample):
    reg = load_register(sample)
    create_record(reg, {"id": "JTBD-2-new", "status": "Proposed"})
    assert get(reg, "JTBD-2-new")["status"] == "Proposed"


def test_create_refuses_blank_or_duplicate_id(sample):
    reg = load_register(sample)
    with pytest.raises(RegistryAccessError):
        create_record(reg, {"status": "Proposed"})  # no id
    with pytest.raises(RegistryAccessError):
        create_record(reg, {"id": "JTBD-1-example"})  # duplicate


# --- founder-mode: two transitions, saved once (D2) --------------------------


def test_choose_and_fund_record_both_transitions(sample):
    reg = load_register(sample)
    apply_transition(
        reg,
        "JTBD-1-example",
        action="choose",
        by="cto-agent",
        at="t1",
        fields={"chosen-solution": "SOL-9"},
        approval=_approval(),
    )
    apply_transition(
        reg,
        "JTBD-1-example",
        action="accept-cost",
        by="ceo-agent",
        at="t2",
        fields={"cost-accepted": True},
        approval=_approval(),
    )
    actions = [t["action"] for t in get(reg, "JTBD-1-example")["transitions"]]
    assert actions[-2:] == ["choose", "accept-cost"]  # both kept, not collapsed


# --- A.5 transition shape: changes list (multi-field) + structured hat --------


def test_multi_field_change_records_a_changes_entry_each(sample):
    reg = load_register(sample)
    # accept-cost sets several fields at once -> one changes entry per field (A.5), the
    # audit the flat single-field trio could not carry.
    apply_transition(
        reg,
        "JTBD-1-example",
        action="accept-cost",
        by="ceo-agent",
        at="t",
        fields={"cost-accepted": True, "budget": 42},
        approval=_approval(),
    )
    t = get(reg, "JTBD-1-example")["transitions"][-1]
    by_field = {c["field"]: c for c in t["changes"]}
    assert by_field["cost-accepted"]["new"] is True and "old" not in by_field["cost-accepted"]
    assert by_field["budget"]["new"] == 42 and "old" not in by_field["budget"]
    assert "field" not in t  # flat trio superseded


def test_structured_hat_is_recorded_on_the_approval(sample):
    reg = load_register(sample)
    approval = {**_approval(), "via": "hat", "hat": "ceo"}
    apply_transition(
        reg,
        "JTBD-1-example",
        action="accept-cost",
        by="ceo-agent",
        at="t",
        fields={"cost-accepted": True},
        approval=approval,
    )
    assert get(reg, "JTBD-1-example")["transitions"][-1]["approval"]["hat"] == "ceo"


def test_blank_hat_is_refused(sample):
    reg = load_register(sample)
    with pytest.raises(RegistryAccessError, match="hat"):
        apply_transition(
            reg,
            "JTBD-1-example",
            action="accept-cost",
            by="ceo-agent",
            at="t",
            approval={**_approval(), "hat": "  "},
        )


# --- the contract suite ------------------------------------------------------


def test_contract_suite_passes_for_the_file_backend(sample, tmp_path):
    failures = run_contract_suite(
        load=load_register, save=save_register, sample_path=sample, tmp_path=tmp_path
    )
    assert failures == []


# --- rac 2.2: the RegistryBackend seam (Protocol + FileBackend) ---------------

from otaman_core.registry_access import FileBackend, RegistryBackend  # noqa: E402


def test_file_backend_satisfies_the_protocol():
    # runtime_checkable: the first backend IS a RegistryBackend
    assert isinstance(FileBackend(), RegistryBackend)


def test_contract_suite_passes_for_the_backend_object(sample, tmp_path):
    # the preferred 2.2 form: run the suite against a RegistryBackend, not raw callables
    assert run_contract_suite(backend=FileBackend(), sample_path=sample, tmp_path=tmp_path) == []


def test_file_backend_round_trips_byte_equivalent(sample):
    be = FileBackend()
    reg = be.load(sample)
    assert dumps(reg) == sample.read_text(encoding="utf-8")  # the seam preserves annotations


def test_suite_requires_a_backend_or_callables(sample, tmp_path):
    with pytest.raises(RegistryAccessError, match="backend="):
        run_contract_suite(sample_path=sample, tmp_path=tmp_path)  # neither form given


# --- cli rac-1.2 rewire findings (1 missing_ok, 3 omit-old, 5 fast read) ------

from otaman_core.registry_access import read_register_fast  # noqa: E402


def test_load_register_missing_ok(tmp_path):
    missing = tmp_path / "nope" / "outcomes.yaml"
    with pytest.raises(FileNotFoundError):
        load_register(missing)  # default raises
    reg = load_register(missing, missing_ok=True)  # create-fresh case
    assert reg.records() == [] and reg.read_only is False


def test_first_set_omits_old(sample):
    reg = load_register(sample)
    # 'chosen-solution' is absent on the record -> first set must omit old, not old: None
    apply_transition(
        reg,
        "JTBD-1-example",
        action="choose",
        by="cto",
        at="t",
        fields={"chosen-solution": "SOL-1"},
        approval=_approval(),
    )
    ch = get(reg, "JTBD-1-example")["transitions"][-1]["changes"][0]
    assert "old" not in ch and ch["new"] == "SOL-1"
    # a subsequent change DOES record old
    apply_transition(
        reg,
        "JTBD-1-example",
        action="choose",
        by="cto",
        at="t2",
        fields={"chosen-solution": "SOL-2"},
        approval=_approval(),
    )
    ch2 = get(reg, "JTBD-1-example")["transitions"][-1]["changes"][0]
    assert ch2["old"] == "SOL-1" and ch2["new"] == "SOL-2"


def test_fast_read_is_read_only_and_refused_by_save(sample, tmp_path):
    reg = read_register_fast(sample)
    assert reg.read_only is True
    assert get(reg, "JTBD-1-example")["status"] == "Approved"  # reads fine
    with pytest.raises(RegistryAccessError, match="display"):
        save_register(reg, tmp_path / "out.yaml")  # refuses a lossy save


def test_fast_read_missing_ok_default(tmp_path):
    reg = read_register_fast(tmp_path / "nope.yaml")
    assert reg.records() == [] and reg.read_only is True


# --- rac 1.3: validate_register (report, flag-don't-break; A.4 enum + remainder) ---

from otaman_core.registry_access import validate_register  # noqa: E402


def _reg(records):
    from otaman_core.registry_access import Register

    return Register(data={"outcomes": records}, records_key="outcomes")


def test_validate_clean_record_has_no_violations():
    reg = _reg(
        [
            {
                "id": "JTBD-1",
                "status": "Done",
                "category": "X",
                "statement": {"as-a": "u", "i-want-to": "x", "so-i-can": "y"},
            }
        ]
    )
    assert validate_register(reg) == []


def test_validate_flags_done_partial_with_migration_hint():
    reg = _reg([{"id": "JTBD-7", "status": "Done-Partial"}])
    v = validate_register(reg)
    assert any("JTBD-7" in m and "abolished" in m and "remainder" in m for m in v)


def test_validate_flags_unknown_status():
    reg = _reg([{"id": "JTBD-9", "status": "Considering"}])  # solution vocab, not A.4
    assert (
        any("JTBD-9" in m and "status" in m for m in v) if (v := validate_register(reg)) else False
    )


def test_validate_accepts_the_six_enum_values():
    for s in ("Drafting", "Backlog", "Approved", "In-Progress", "Done", "Retired"):
        assert validate_register(_reg([{"id": "x", "status": s}])) == []


def test_validate_flags_remainder_wrong_type():
    reg = _reg([{"id": "JTBD-1", "status": "Done", "remainder": ["not", "a", "string"]}])
    assert any("remainder" in m for m in validate_register(reg))


def test_validate_flags_a_malformed_changes_entry():
    # the A.5 transition sub-shape is LIVE, not a vacuous declaration: a changes entry
    # missing `new` is flagged, while a well-formed one (old omitted) passes.
    bad = _reg([{"id": "J", "status": "Done", "transitions": [{"changes": [{"field": "x"}]}]}])
    assert any("changes" in m or "new" in m for m in validate_register(bad))
    good = _reg(
        [{"id": "J", "status": "Done", "transitions": [{"changes": [{"field": "x", "new": 1}]}]}]
    )
    assert validate_register(good) == []


def test_validate_is_a_report_not_a_gate():
    # a register full of violations returns a list, never raises
    reg = _reg([{"id": "a", "status": "Done-Partial"}, {"status": "Nope"}, "junk"])
    v = validate_register(reg)
    assert isinstance(v, list) and len(v) >= 3
