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
    check_dispatch_gate_at,
    check_merge_gate,
    has_approval,
    has_cto,
    has_scr_approval,
    is_forward_transition,
    is_research,
    next_stage,
    openspec_is_unreadable,
    parse_spec_policy,
    ratifications_in_month,
    ratify,
    read_delivery,
    read_openspec,
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

# merge-ready: BOTH signals present (spec-agent ruling 20260930) — the spec-approve
# act writes approved_by AND advances stage to spec-approved.
APPROVED = {"stage": "spec-approved", "approved_by": "roman (20260907T121620)"}
AUTHORED_UNAPPROVED = {"stage": "authored"}
BLOCK = SpecPolicy(enforcement="block")
WARN = SpecPolicy(enforcement="warn")
SELFWAIVE = SpecPolicy(enforcement="self-waive")


class TestMergeGate:
    def test_unapproved_delta_refused_in_block(self):
        d = check_merge_gate(AUTHORED_UNAPPROVED, BLOCK)
        assert not d.allowed
        assert any("spec-approved" in v for v in d.violations)

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

    # --- two-signal conjunct + disagreement (spec-agent ruling 20260930) ---

    def test_field_present_but_stage_before_spec_approved_refuses(self):
        # the sghc mislabel shape: approved_by set while stage is only 'approved'
        change = {"stage": "approved", "approved_by": "roman (scr)"}
        d = check_merge_gate(change, BLOCK)
        assert not d.allowed
        assert any("disagree" in v and "stage" in v for v in d.violations)

    def test_stage_advanced_but_field_missing_refuses(self):
        change = {"stage": "spec-approved"}  # stage right, attestation missing
        d = check_merge_gate(change, BLOCK)
        assert not d.allowed
        assert any("disagree" in v for v in d.violations)

    def test_disagreement_refuses_even_under_warn(self):
        # a disagreement is a data-integrity fault, not a policy state — warn must
        # NOT downgrade it to allowed-with-notice (nss: never silently reconciled)
        change = {"stage": "approved", "approved_by": "roman (scr)"}
        assert not check_merge_gate(change, WARN).allowed
        assert not check_merge_gate(change, SELFWAIVE).allowed

    def test_both_absent_is_mode_decided_not_disagreement(self):
        # neither signal → the normal not-yet-authorized state → mode governs
        change = {"stage": "authored"}
        assert not check_merge_gate(change, BLOCK).allowed
        assert check_merge_gate(change, WARN).allowed  # waived, loud

    # --- authored-stage arm (scr_approved_by): the regression fix 20261001 ---

    def test_authoring_merge_accepted_with_scr_approved_by(self):
        # an authored change has no approved_by BY DESIGN; scr_approved_by is the
        # honored field there. This is what #91 wrongly refused (instruction-regen).
        change = {"stage": "authored", "scr_approved_by": "roman (SCR broadcast, authoring)"}
        d = check_merge_gate(change, BLOCK)
        assert d.allowed and not d.waived

    def test_authoring_merge_before_spec_approved_stages(self):
        # the arm covers every pre-spec-approved stage, not just 'authored'
        for stage in ("proposed", "approved", "authored"):
            change = {"stage": stage, "scr_approved_by": "roman (SCR)"}
            assert check_merge_gate(change, BLOCK).allowed, stage

    def test_approved_by_before_spec_approved_still_refuses(self):
        # approved_by is the spec-approve field — setting it while authoring is the
        # mislabel disagreement, hard-refused even with scr_approved_by also present
        change = {
            "stage": "authored",
            "scr_approved_by": "roman (SCR)",
            "approved_by": "roman (mislabeled)",
        }
        d = check_merge_gate(change, BLOCK)
        assert not d.allowed
        assert any("disagree" in v for v in d.violations)

    def test_has_scr_approval(self):
        assert has_scr_approval({"scr_approved_by": "roman (SCR)"})
        assert not has_scr_approval({"scr_approved_by": "  "})
        assert not has_scr_approval({"approved_by": "roman"})  # the OTHER field
        assert not has_scr_approval({})


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

    def test_ratified_but_not_spec_approved_names_the_attestation(self):
        # ratify-spec-approve-split 1.2: don't call an existing attestation absent
        change = {
            "stage": "approved",
            "change": "my-change",
            "ratified": True,
            "ratified_by": "roman",
            "ratified_at": "2026-09-15T10:00:00",
        }
        d = check_dispatch_gate(change, BLOCK)
        assert not d.allowed
        v = " ".join(d.violations)
        assert "ratified by roman" in v
        assert "2026-09-15T10:00:00" in v
        assert "otaman spec approve my-change" in v
        assert "lack a HITL approval" not in v  # the human attestation is NOT called absent

    def test_unratified_authored_keeps_generic_message(self):
        d = check_dispatch_gate({"stage": "authored"}, BLOCK)
        assert not d.allowed
        assert any("lack a HITL approval" in v for v in d.violations)

    def test_ratifier_parsed_from_approved_by_marker(self):
        # gate 2.1 finding: no ratified_by field, but the name is in the marker
        change = {
            "stage": "approved",
            "change": "contradictory-change",
            "ratified": True,
            "ratified_at": "2026-09-13T21:05:54Z",
            "approved_by": "ratified: starikov@inprimex.com — merged before approval",
        }
        d = check_dispatch_gate(change, BLOCK)
        v = " ".join(d.violations)
        assert "ratified by starikov@inprimex.com" in v  # parsed, not "the ratifier"
        assert "otaman spec approve contradictory-change" in v

    def test_ratified_message_degrades_without_who_when(self):
        # ratified flag set but no ratified_by AND no parseable marker → generic,
        # still names the condition (never "absent")
        d = check_dispatch_gate({"stage": "approved", "ratified": True}, BLOCK)
        v = " ".join(d.violations)
        assert "ratified by the ratifier" in v and "spec approve" in v


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

    def test_apply_ratification_sets_marker_and_attestation(self):
        r = ratify("c", by="roman", reason="retrofit", at="2026-09-07T12:00:00")
        out = apply_ratification({"stage": "proposed"}, r)
        assert out["stage"] == "approved"  # below approved → floored up to approved
        assert out["ratified"] is True
        assert out["ratified_by"] == "roman"
        assert out["ratified_at"] == "2026-09-07T12:00:00"
        assert "ratified:" in out["approved_by"] and "roman" in out["approved_by"]

    def test_apply_ratification_monotonic_never_demotes(self):
        # ratify-spec-approve-split 1.1: target = max(current, approved) by index
        r = ratify("c", by="roman", reason="x", at="t")
        assert apply_ratification({"stage": "spec-approved"}, r)["stage"] == "spec-approved"
        assert apply_ratification({"stage": "implemented"}, r)["stage"] == "implemented"
        assert apply_ratification({"stage": "authored"}, r)["stage"] == "authored"
        assert apply_ratification({"stage": "approved"}, r)["stage"] == "approved"
        assert apply_ratification({"stage": "proposed"}, r)["stage"] == "approved"
        assert apply_ratification({"stage": "pre-proposal"}, r)["stage"] == "approved"
        assert apply_ratification({}, r)["stage"] == "approved"  # absent → approved

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


# --- _load_yaml: fastest safe loader (cli #164 perf) --------------------------


class TestLoadYaml:
    def test_read_openspec_parses_correctly(self, tmp_path: Path):
        p = tmp_path / ".openspec.yaml"
        p.write_text("stage: approved\napproved_by: roman\n", encoding="utf-8")
        assert read_openspec(p) == {"stage": "approved", "approved_by": "roman"}

    def test_absent_or_non_mapping_is_empty(self, tmp_path: Path):
        assert read_openspec(tmp_path / "nope.yaml") == {}
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n", encoding="utf-8")
        assert read_openspec(p) == {}

    def test_uses_libyaml_when_available(self, tmp_path: Path, monkeypatch):
        # the C loader is what makes the 136x/console-Home read cheap; assert we
        # actually select it (and still parse) when the extension is present.
        if getattr(yaml, "CSafeLoader", None) is None:
            pytest.skip("libyaml (CSafeLoader) not built into this PyYAML")
        used: dict[str, object] = {}
        real_load = yaml.load

        def spy_load(stream, Loader):  # noqa: N803 — mirrors yaml.load's kwarg
            used["loader"] = Loader
            return real_load(stream, Loader=Loader)

        monkeypatch.setattr(yaml, "load", spy_load)
        p = tmp_path / ".openspec.yaml"
        p.write_text("stage: proposed\n", encoding="utf-8")
        assert read_openspec(p) == {"stage": "proposed"}
        assert used["loader"] is yaml.CSafeLoader


# --- dispatch gate fails CLOSED on an unparseable .openspec.yaml (sghc) --------

# The malformed value that started the incident: an unquoted colon inside a scalar
# raises a scanner error ("mapping values are not allowed here").
_BAD_YAML = "stage: authored\nrequested_by: roman: harden hook-c\n"


class TestOpenspecIsUnreadable:
    def test_absent_is_not_unreadable(self, tmp_path):
        assert openspec_is_unreadable(tmp_path / "nope.yaml") is False

    def test_valid_is_not_unreadable(self, tmp_path):
        p = tmp_path / ".openspec.yaml"
        p.write_text("stage: authored\n", encoding="utf-8")
        assert openspec_is_unreadable(p) is False

    def test_empty_is_not_unreadable(self, tmp_path):
        # an empty file parses to None — a change with no stage, not a parse failure
        p = tmp_path / ".openspec.yaml"
        p.write_text("", encoding="utf-8")
        assert openspec_is_unreadable(p) is False

    def test_parse_error_is_unreadable(self, tmp_path):
        p = tmp_path / ".openspec.yaml"
        p.write_text(_BAD_YAML, encoding="utf-8")
        assert openspec_is_unreadable(p) is True

    def test_non_mapping_is_unreadable(self, tmp_path):
        p = tmp_path / ".openspec.yaml"
        p.write_text("- a\n- b\n", encoding="utf-8")
        assert openspec_is_unreadable(p) is True


class TestDispatchGateFailsClosed:
    def _p(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / ".openspec.yaml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_absent_file_allows(self, tmp_path):
        # no .openspec.yaml means no gate is declared — nothing to verify
        d = check_dispatch_gate_at(
            tmp_path / "nope.yaml", parse_spec_policy({"enforcement": "block"})
        )
        assert d.allowed is True

    def test_unparseable_refuses_under_block(self, tmp_path):
        p = self._p(tmp_path, _BAD_YAML)
        d = check_dispatch_gate_at(p, parse_spec_policy({"enforcement": "block"}))
        assert d.allowed is False
        assert any("does not parse" in v for v in d.violations)

    def test_unparseable_refuses_even_under_warn(self, tmp_path):
        # THE regression: warn must NOT downgrade an inability-to-verify to a pass.
        # This is the fail-open the sghc incident exposed.
        p = self._p(tmp_path, _BAD_YAML)
        d = check_dispatch_gate_at(p, parse_spec_policy({"enforcement": "warn"}))
        assert d.allowed is False

    def test_unparseable_refuses_even_under_self_waive(self, tmp_path):
        p = self._p(tmp_path, _BAD_YAML)
        d = check_dispatch_gate_at(p, parse_spec_policy({"enforcement": "self-waive"}))
        assert d.allowed is False

    def test_authored_refuses_under_block(self, tmp_path):
        # a READABLE authored change still gates normally (mode-decided)
        p = self._p(tmp_path, "stage: authored\n")
        assert (
            check_dispatch_gate_at(p, parse_spec_policy({"enforcement": "block"})).allowed is False
        )

    def test_authored_warns_but_allows_under_warn(self, tmp_path):
        p = self._p(tmp_path, "stage: authored\n")
        d = check_dispatch_gate_at(p, parse_spec_policy({"enforcement": "warn"}))
        assert d.allowed is True and d.waived is True

    def test_spec_approved_allows(self, tmp_path):
        p = self._p(tmp_path, "stage: spec-approved\n")
        assert (
            check_dispatch_gate_at(p, parse_spec_policy({"enforcement": "block"})).allowed is True
        )
