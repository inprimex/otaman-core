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
    # chosen-solution was absent -> old omitted (finding 3), new recorded
    assert t["field"] == "chosen-solution" and "old" not in t and t["new"] == "SOL-9"
    assert get(reg, "JTBD-1-example")["chosen-solution"] == "SOL-9"
    assert "status" not in t  # to_status=None -> no status change recorded


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


# --- the contract suite ------------------------------------------------------


def test_contract_suite_passes_for_the_file_backend(sample, tmp_path):
    failures = run_contract_suite(
        load=load_register, save=save_register, sample_path=sample, tmp_path=tmp_path
    )
    assert failures == []


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
    entry = get(reg, "JTBD-1-example")["transitions"][-1]
    assert "old" not in entry and entry["new"] == "SOL-1"
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
    entry2 = get(reg, "JTBD-1-example")["transitions"][-1]
    assert entry2["old"] == "SOL-1" and entry2["new"] == "SOL-2"


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


def test_validate_is_a_report_not_a_gate():
    # a register full of violations returns a list, never raises
    reg = _reg([{"id": "a", "status": "Done-Partial"}, {"status": "Nope"}, "junk"])
    v = validate_register(reg)
    assert isinstance(v, list) and len(v) >= 3
