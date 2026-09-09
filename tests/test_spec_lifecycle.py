"""Tests for otaman_core.spec_lifecycle — the lifecycle substrate (SLE 1.1-1.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    pytest.skip("PyYAML not installed", allow_module_level=True)

from otaman_core.human_roster import HumanRosterEntry
from otaman_core.spec_lifecycle import (
    DELIVERY_MODES,
    ENFORCEMENT_MODES,
    PROCESS_LEVELS,
    STAGES,
    AutoArchiveDecision,
    GateDecision,
    Ratification,
    SpecLifecycleError,
    SpecPolicy,
    TransitionValidation,
    amendment_reenters_review,
    apply_auto_archive,
    apply_ratification,
    apply_spec_approved,
    auto_archive_decision,
    check_archive_gate,
    check_dispatch_gate,
    check_merge_gate,
    has_approval,
    has_cto,
    is_forward_transition,
    is_research,
    next_stage,
    parse_spec_policy,
    ratifications_in_month,
    ratify,
    read_delivery,
    read_stage,
    resolve_delivery,
    resolve_spec_approver,
    resolve_spec_policy,
    set_stage,
    solutions_stage_required,
    spec_approved_reached,
    stage_index,
    validate_spec_approved_transition,
)

# --- 1.1 stage model ----------------------------------------------------------


class TestStageModel:
    def test_stage_order(self):
        assert STAGES[0] == "pre-proposal"
        assert STAGES[-1] == "archived"
        assert "spec-approved" in STAGES

    def test_stage_index_and_forward(self):
        assert stage_index("proposed") < stage_index("approved")
        assert is_forward_transition("authored", "spec-approved")
        assert not is_forward_transition("dispatched", "approved")
        assert not is_forward_transition("approved", "approved")

    def test_unknown_stage_raises(self):
        with pytest.raises(SpecLifecycleError):
            stage_index("banana")

    def test_next_stage(self):
        assert next_stage("authored") == "spec-approved"
        assert next_stage("archived") is None

    def test_is_research(self):
        assert is_research({"stage": "pre-proposal"})
        assert is_research({"stage": "proposed", "skip_specs": True})
        assert is_research({"research": True})
        assert not is_research({"stage": "authored"})

    def test_spec_approved_reached(self):
        assert spec_approved_reached({"stage": "spec-approved"})
        assert spec_approved_reached({"stage": "dispatched"})
        assert not spec_approved_reached({"stage": "authored"})
        assert not spec_approved_reached({"stage": "nonsense"})
        assert not spec_approved_reached({})

    def test_has_approval(self):
        assert has_approval({"approved_by": "roman (otaman -i, 20260907T121620)"})
        assert not has_approval({"approved_by": ""})
        assert not has_approval({})

    def test_amendment_reentry(self):
        assert amendment_reenters_review(touches_capability_delta=True, is_research_change=False)
        assert not amendment_reenters_review(
            touches_capability_delta=False, is_research_change=False
        )
        # research is always exempt
        assert not amendment_reenters_review(touches_capability_delta=True, is_research_change=True)


# --- .openspec.yaml read/write (repo is truth, D1) ---------------------------


class TestOpenspecReadWrite:
    def _write(self, path: Path, data: dict) -> None:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def test_read_stage(self, tmp_path):
        p = tmp_path / ".openspec.yaml"
        self._write(p, {"stage": "approved", "spec_owner": "spec-agent"})
        assert read_stage(p) == "approved"

    def test_read_stage_absent(self, tmp_path):
        assert read_stage(tmp_path / "nope.yaml") is None

    def test_set_stage_preserves_other_keys(self, tmp_path):
        p = tmp_path / ".openspec.yaml"
        self._write(p, {"stage": "authored", "spec_owner": "spec-agent", "outcome": "JTBD-99"})
        set_stage(p, "spec-approved")
        got = yaml.safe_load(p.read_text())
        assert got["stage"] == "spec-approved"
        assert got["spec_owner"] == "spec-agent"  # preserved
        assert got["outcome"] == "JTBD-99"  # preserved

    def test_set_stage_rejects_unknown(self, tmp_path):
        p = tmp_path / ".openspec.yaml"
        self._write(p, {"stage": "authored"})
        with pytest.raises(SpecLifecycleError):
            set_stage(p, "banana")

    def test_repo_is_truth(self, tmp_path):
        # a change's stage comes from its .openspec.yaml, not any external claim
        p = tmp_path / ".openspec.yaml"
        self._write(p, {"stage": "authored"})
        assert read_stage(p) == "authored"  # repo value governs


# --- D5 spec-approved approver resolution ------------------------------------


class TestSpecApprover:
    def test_cto_hat_resolves(self):
        roster = [HumanRosterEntry(name="Roman", roles=["cto", "cofounder"])]
        assert resolve_spec_approver(roster, "Roman") is not None

    def test_default_approver_stands_in_without_cto(self):
        roster = [HumanRosterEntry(name="Roman", roles=["approver"])]
        assert resolve_spec_approver(roster, "Roman") is not None

    def test_non_approver_rejected(self):
        roster = [HumanRosterEntry(name="Dev", roles=["developer"])]
        assert resolve_spec_approver(roster, "Dev") is None

    def test_unresolved_identity_is_none(self):
        roster = [HumanRosterEntry(name="Roman", roles=["cto"])]
        assert resolve_spec_approver(roster, "someone-else") is None

    def test_has_cto(self):
        assert has_cto([HumanRosterEntry(name="R", roles=["cto"])])
        assert not has_cto([HumanRosterEntry(name="R", roles=["approver"])])


# --- 1.2 spec_policy ----------------------------------------------------------


class TestSpecPolicyParse:
    def test_defaults_are_l1_warn(self):
        p = parse_spec_policy(None)
        assert p.enforcement == "warn"
        assert p.process_level == "spec-first"
        assert p.mandatory_proposal is True

    def test_parses_enforcement_and_level(self):
        p = parse_spec_policy({"enforcement": "block", "process": {"level": "outcomes"}})
        assert p.enforcement == "block"
        assert p.process_level == "outcomes"

    def test_invalid_enforcement_falls_back(self):
        assert parse_spec_policy({"enforcement": "nuke"}).enforcement == "warn"

    def test_invalid_level_falls_back(self):
        assert parse_spec_policy({"process": {"level": "l9"}}).process_level == "spec-first"

    def test_enum_membership(self):
        assert "self-waive" in ENFORCEMENT_MODES
        assert "verified" in PROCESS_LEVELS

    def test_lists_parsed(self):
        p = parse_spec_policy({"approvers": ["cto"], "unapproved_stages": ["pre-proposal"]})
        assert p.approvers == ("cto",)
        assert p.unapproved_stages == ("pre-proposal",)


class TestSpecPolicyCascade:
    def test_program_overrides_org_per_key(self):
        org = {"enforcement": "warn", "mandatory_proposal": True}
        program = {"enforcement": "block"}
        p = resolve_spec_policy(org, program)
        assert p.enforcement == "block"  # program wins
        assert p.mandatory_proposal is True  # inherited from org

    def test_program_inherits_when_empty(self):
        org = {"enforcement": "block"}
        p = resolve_spec_policy(org, {})
        assert p.enforcement == "block"

    def test_process_level_cascades(self):
        org = {"process": {"level": "solutions"}}
        program = {"process": {"level": "verified"}}
        assert resolve_spec_policy(org, program).process_level == "verified"

    def test_absent_both_is_default(self):
        assert resolve_spec_policy(None, None).enforcement == "warn"


class TestProcessLevelR1:
    def test_l1_never_heavier(self):
        p = SpecPolicy(process_level="spec-first")
        assert not solutions_stage_required(p, multi_repo=True, impact_ge_l=True)

    def test_solutions_level_triggers_on_multi_repo(self):
        p = SpecPolicy(process_level="solutions")
        assert solutions_stage_required(p, multi_repo=True)

    def test_solutions_level_small_single_repo_skips(self):
        p = SpecPolicy(process_level="solutions")
        assert not solutions_stage_required(p)  # no trigger fires

    def test_impact_or_estimate_trigger(self):
        p = SpecPolicy(process_level="outcomes")
        assert solutions_stage_required(p, impact_ge_l=True)
        assert solutions_stage_required(p, estimate_medium_plus=True)


# --- 1.3 gates ----------------------------------------------------------------

APPROVED = {"stage": "approved", "approved_by": "roman (20260907T121620)"}
AUTHORED_UNAPPROVED = {"stage": "authored"}
BLOCK = SpecPolicy(enforcement="block")
WARN = SpecPolicy(enforcement="warn")
SELFWAIVE = SpecPolicy(enforcement="self-waive")


class TestMergeGate:
    def test_unapproved_delta_refused_in_block(self):
        d = check_merge_gate(AUTHORED_UNAPPROVED, BLOCK)
        assert not d.allowed
        assert any("approval" in v for v in d.violations)

    def test_approved_passes(self):
        d = check_merge_gate(APPROVED, BLOCK)
        assert d.allowed and not d.waived

    def test_research_exempt(self):
        d = check_merge_gate({"stage": "pre-proposal"}, BLOCK)
        assert d.allowed

    def test_no_delta_exempt(self):
        d = check_merge_gate(AUTHORED_UNAPPROVED, BLOCK, has_capability_delta=False)
        assert d.allowed

    def test_self_waive_is_visible_not_silent(self):
        d = check_merge_gate(AUTHORED_UNAPPROVED, SELFWAIVE)
        assert d.allowed and d.waived
        assert d.notices and any("self-waive" in n for n in d.notices)

    def test_warn_proceeds_loudly(self):
        d = check_merge_gate(AUTHORED_UNAPPROVED, WARN)
        assert d.allowed and d.waived
        assert d.notices

    def test_outcome_link_enforced_when_jtbd_enabled(self):
        # approved but no outcome, JTBD on → refuse in block
        d = check_merge_gate(APPROVED, BLOCK, jtbd_enabled=True)
        assert not d.allowed
        assert any("outcome" in v for v in d.violations)

    def test_outcome_link_skipped_when_jtbd_disabled(self):
        d = check_merge_gate(APPROVED, BLOCK, jtbd_enabled=False)
        assert d.allowed

    def test_outcome_present_but_not_approved(self):
        change = {**APPROVED, "outcome": "JTBD-99"}
        d = check_merge_gate(change, BLOCK, jtbd_enabled=True, outcome_ok=False)
        assert not d.allowed
        assert any("chosen solution" in v for v in d.violations)

    def test_outcome_present_and_approved_passes(self):
        change = {**APPROVED, "outcome": "JTBD-99"}
        d = check_merge_gate(change, BLOCK, jtbd_enabled=True, outcome_ok=True)
        assert d.allowed


class TestDispatchGate:
    def test_refused_without_spec_approved(self):
        d = check_dispatch_gate({"stage": "authored"}, BLOCK)
        assert not d.allowed
        assert any("spec-approved" in v for v in d.violations)

    def test_allowed_at_spec_approved(self):
        d = check_dispatch_gate({"stage": "spec-approved"}, BLOCK)
        assert d.allowed

    def test_research_exempt(self):
        d = check_dispatch_gate({"stage": "pre-proposal"}, BLOCK)
        assert d.allowed

    def test_warn_mode_proceeds(self):
        d = check_dispatch_gate({"stage": "authored"}, WARN)
        assert d.allowed and d.waived


class TestArchiveGate:
    def test_unapproved_refused_in_block(self):
        d = check_archive_gate({"stage": "implemented"}, BLOCK)
        assert not d.allowed

    def test_approved_passes(self):
        d = check_archive_gate({**APPROVED, "stage": "verified"}, BLOCK)
        assert d.allowed

    def test_research_exempt(self):
        d = check_archive_gate({"stage": "pre-proposal"}, BLOCK)
        assert d.allowed


class TestCiLessLifecycle:
    def test_gates_are_local_no_integration_args(self):
        # All three gates decide from the change + policy alone — no CI/git-host
        # handle is ever passed. A zero-integration tenant is fully enforced.
        assert check_merge_gate(APPROVED, BLOCK).allowed
        assert check_dispatch_gate({"stage": "spec-approved"}, BLOCK).allowed
        assert check_archive_gate({**APPROVED, "stage": "verified"}, BLOCK).allowed
        assert not check_merge_gate(AUTHORED_UNAPPROVED, BLOCK).allowed


# --- 1.4 ratify ---------------------------------------------------------------


class TestRatify:
    def test_mints_ratification(self):
        r = ratify(
            "my-change", by="roman", reason="merged before approval", at="2026-09-07T12:00:00"
        )
        assert isinstance(r, Ratification)
        assert r.ratified is True
        assert r.by == "roman" and r.reason == "merged before approval"

    def test_requires_human(self):
        with pytest.raises(SpecLifecycleError):
            ratify("c", by="", reason="x", at="2026-09-07T12:00:00")

    def test_requires_reason(self):
        with pytest.raises(SpecLifecycleError):
            ratify("c", by="roman", reason="   ", at="2026-09-07T12:00:00")

    def test_apply_ratification_sets_approved_and_marker(self):
        r = ratify("c", by="roman", reason="retrofit", at="2026-09-07T12:00:00")
        out = apply_ratification({"stage": "implemented"}, r)
        assert out["stage"] == "approved"
        assert out["ratified"] is True
        assert "ratified:" in out["approved_by"]
        assert "roman" in out["approved_by"]

    def test_apply_ratification_is_pure(self):
        r = ratify("c", by="roman", reason="x", at="2026-09-07T12:00:00")
        original = {"stage": "implemented"}
        apply_ratification(original, r)
        assert original == {"stage": "implemented"}  # not mutated

    def test_ratifications_in_month_count(self):
        rs = [
            ratify("a", by="r", reason="x", at="2026-09-01T10:00:00"),
            ratify("b", by="r", reason="y", at="2026-09-30T23:00:00"),
            ratify("c", by="r", reason="z", at="2026-08-15T10:00:00"),
        ]
        assert ratifications_in_month(rs, year=2026, month=9) == 2
        assert ratifications_in_month(rs, year=2026, month=8) == 1
        assert ratifications_in_month(rs, year=2026, month=7) == 0


# --- GateDecision shape (the announced contract) -----------------------------


class TestGateDecisionShape:
    def test_clean_decision_fields(self):
        d = check_merge_gate(APPROVED, BLOCK)
        assert isinstance(d, GateDecision)
        assert d.gate == "merge"
        assert d.violations == () and d.notices == ()


# --- IHC 2.2: spec-approved transition validation ----------------------------

CTO = HumanRosterEntry(name="Roman", roles=["cto", "cofounder"])


class TestSpecApprovedTransition:
    def test_valid_from_authored_with_approver(self):
        v = validate_spec_approved_transition({"stage": "authored"}, approver=CTO)
        assert isinstance(v, TransitionValidation)
        assert v.valid and v.reasons == ()

    def test_refused_from_wrong_stage(self):
        v = validate_spec_approved_transition({"stage": "proposed"}, approver=CTO)
        assert not v.valid
        assert any("authored" in r for r in v.reasons)

    def test_refused_without_eligible_approver(self):
        v = validate_spec_approved_transition({"stage": "authored"}, approver=None)
        assert not v.valid
        assert any("approver" in r for r in v.reasons)

    def test_research_never_enters(self):
        v = validate_spec_approved_transition({"stage": "pre-proposal"}, approver=CTO)
        assert not v.valid
        assert any("research" in r for r in v.reasons)

    def test_multiple_reasons_accumulate(self):
        v = validate_spec_approved_transition({"stage": "proposed"}, approver=None)
        assert not v.valid and len(v.reasons) == 2

    def test_apply_spec_approved_advances_and_records(self):
        out = apply_spec_approved({"stage": "authored"}, CTO)
        assert out["stage"] == "spec-approved"
        assert out["spec_approved_by"] == "Roman"

    def test_apply_spec_approved_is_pure(self):
        original = {"stage": "authored"}
        apply_spec_approved(original, CTO)
        assert original == {"stage": "authored"}

    def test_round_trip_validate_then_apply_reaches_dispatchable(self):
        change = {"stage": "authored"}
        assert validate_spec_approved_transition(change, approver=CTO).valid
        advanced = apply_spec_approved(change, CTO)
        # now the dispatch gate passes (spec_approved_reached is True)
        assert spec_approved_reached(advanced)


# --- console-lifecycle-actions 2.1: delivery mode + auto-archive -------------

_APPROVED_VERIFIED = {"stage": "verified", "approved_by": "roman (x)"}


class TestDeliveryMode:
    def test_modes(self):
        assert DELIVERY_MODES == ("hitl", "auto")

    def test_read_delivery_explicit(self):
        assert read_delivery({"delivery": "auto"}) == "auto"
        assert read_delivery({"delivery": "hitl"}) == "hitl"

    def test_read_delivery_absent_or_invalid_is_none(self):
        assert read_delivery({}) is None
        assert read_delivery({"delivery": "bogus"}) is None

    def test_resolve_explicit_wins(self):
        assert resolve_delivery({"delivery": "auto"}, SpecPolicy(delivery_default="hitl")) == "auto"

    def test_resolve_falls_back_to_policy_default(self):
        assert resolve_delivery({}, SpecPolicy(delivery_default="auto")) == "auto"

    def test_resolve_default_hitl(self):
        assert resolve_delivery({}, SpecPolicy()) == "hitl"


class TestSpecPolicyDelivery:
    def test_parse_delivery_default(self):
        assert parse_spec_policy({"delivery_default": "auto"}).delivery_default == "auto"

    def test_invalid_delivery_default_falls_back(self):
        assert parse_spec_policy({"delivery_default": "nope"}).delivery_default == "hitl"

    def test_delivery_default_cascades(self):
        # org sets auto, program silent → inherits auto
        p = resolve_spec_policy({"delivery_default": "auto"}, {})
        assert p.delivery_default == "auto"
        # program overrides back to hitl
        p2 = resolve_spec_policy({"delivery_default": "auto"}, {"delivery_default": "hitl"})
        assert p2.delivery_default == "hitl"


class TestAutoArchive:
    def _clean_gate(self):
        return check_archive_gate(_APPROVED_VERIFIED, SpecPolicy(enforcement="block"))

    def test_auto_archives_when_verified_auto_and_gate_clean(self):
        change = {**_APPROVED_VERIFIED, "delivery": "auto"}
        d = auto_archive_decision(change, SpecPolicy(), archive_gate=self._clean_gate())
        assert isinstance(d, AutoArchiveDecision)
        assert d.archive and d.reasons == ()

    def test_hitl_never_auto_archives(self):
        change = {**_APPROVED_VERIFIED, "delivery": "hitl"}
        d = auto_archive_decision(change, SpecPolicy(), archive_gate=self._clean_gate())
        assert not d.archive and any("not 'auto'" in r for r in d.reasons)

    def test_policy_default_auto_enables(self):
        # no explicit delivery, program default auto
        d = auto_archive_decision(
            _APPROVED_VERIFIED, SpecPolicy(delivery_default="auto"), archive_gate=self._clean_gate()
        )
        assert d.archive

    def test_only_from_verified(self):
        change = {"stage": "implemented", "delivery": "auto", "approved_by": "x"}
        gate = check_archive_gate(change, SpecPolicy(enforcement="block"))
        d = auto_archive_decision(change, SpecPolicy(), archive_gate=gate)
        assert not d.archive and any("verified" in r for r in d.reasons)

    def test_refused_when_gate_refuses(self):
        # verified + auto but unapproved → archive gate refuses (block)
        change = {"stage": "verified", "delivery": "auto"}  # no approved_by
        gate = check_archive_gate(change, SpecPolicy(enforcement="block"))
        d = auto_archive_decision(change, SpecPolicy(), archive_gate=gate)
        assert not d.archive and any("refuses" in r for r in d.reasons)

    def test_warn_waiver_is_not_a_clean_pass(self):
        # unapproved + warn mode → gate allowed but WAIVED → auto-archive refused
        change = {"stage": "verified", "delivery": "auto"}
        gate = check_archive_gate(change, SpecPolicy(enforcement="warn"))
        assert gate.allowed and gate.waived  # precondition
        d = auto_archive_decision(change, SpecPolicy(), archive_gate=gate)
        assert not d.archive and any("waiver" in r for r in d.reasons)

    def test_apply_auto_archive(self):
        out = apply_auto_archive({**_APPROVED_VERIFIED, "delivery": "auto"})
        assert out["stage"] == "archived"
        assert "auto" in out["archived_by"]

    def test_apply_auto_archive_is_pure(self):
        original = {**_APPROVED_VERIFIED, "delivery": "auto"}
        snapshot = dict(original)
        apply_auto_archive(original)
        assert original == snapshot
