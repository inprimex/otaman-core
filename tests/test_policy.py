"""Tests for otaman_core.policy — the policy engine (policy-engine 1.1-1.3)."""

from __future__ import annotations

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    pytest.skip("PyYAML not installed", allow_module_level=True)

from otaman_core.policy import (
    DEFAULT_POLICY_NAME,
    GIT_PACK_NARROW_ONLY,
    DoctorFinding,
    EffectivePolicy,
    LooseningViolation,
    Policy,
    PolicyError,
    check_deprecated_standards,
    check_owner_less_branches,
    check_policy_drift,
    compose,
    effective_policy,
    load_policy,
    load_policy_index,
    parse_policy,
    parse_policy_index,
    resolve_branch_owner,
    select_policy_names,
    shipped_index,
    shipped_standard,
)


def _write_policy(meta_root, pack, name, rules):
    d = meta_root / "policy" / pack
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(yaml.safe_dump({"rules": rules}), encoding="utf-8")


def _write_index(meta_root, packs):
    (meta_root / "policy").mkdir(parents=True, exist_ok=True)
    (meta_root / "policy" / "index.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "packs": packs}), encoding="utf-8"
    )


class TestIndexModel:
    def test_parse_index_with_narrow_only(self):
        idx = parse_policy_index(
            {"schema_version": 1, "packs": {"git": {"narrow_only": ["a", "b"]}}}
        )
        assert idx.schema_version == 1
        assert idx.narrow_only("git") == frozenset({"a", "b"})
        assert idx.narrow_only("unknown") == frozenset()

    def test_parse_index_none_is_empty(self):
        assert parse_policy_index(None).packs == {}

    def test_parse_index_hyphen_alias(self):
        idx = parse_policy_index({"packs": {"git": {"narrow-only": ["x"]}}})
        assert idx.narrow_only("git") == frozenset({"x"})

    def test_bad_shapes_raise(self):
        with pytest.raises(PolicyError):
            parse_policy_index(["nope"])
        with pytest.raises(PolicyError):
            parse_policy_index({"packs": {"git": {"narrow_only": "notalist"}}})
        with pytest.raises(PolicyError):
            parse_policy_index({"schema_version": "one"})

    def test_load_index_absent_is_empty(self, tmp_path):
        assert load_policy_index(tmp_path).packs == {}


class TestPolicyModel:
    def test_parse_policy(self):
        p = parse_policy("git", "standard", {"rules": {"force_push_forbidden": True}})
        assert p.pack == "git" and p.name == "standard"
        assert p.rules["force_push_forbidden"] is True

    def test_pack_name_mismatch_raises(self):
        with pytest.raises(PolicyError, match="pack"):
            parse_policy("git", "standard", {"pack": "spec", "rules": {}})
        with pytest.raises(PolicyError, match="name"):
            parse_policy("git", "standard", {"name": "strict", "rules": {}})

    def test_rules_not_mapping_raises(self):
        with pytest.raises(PolicyError, match="rules"):
            parse_policy("git", "standard", {"rules": ["x"]})

    def test_load_policy_absent_is_none(self, tmp_path):
        assert load_policy(tmp_path, "git", "standard") is None

    def test_load_policy_round_trip(self, tmp_path):
        _write_policy(tmp_path, "git", "standard", {"force_push_forbidden": True})
        p = load_policy(tmp_path, "git", "standard")
        assert p.rules == {"force_push_forbidden": True}


class TestCompose:
    def test_narrow_only_tightest_wins_or(self):
        # program off, agent on -> effective on (tighten allowed)
        layers = [
            ("program", Policy("git", "standard", {"force_push_forbidden": False})),
            ("agent", Policy("git", "a", {"force_push_forbidden": True})),
        ]
        eff, viol = compose(layers, {"force_push_forbidden"})
        assert eff.rules["force_push_forbidden"] is True
        assert viol == []

    def test_narrow_only_loosening_refused_and_flagged(self):
        # program on (True), agent tries to loosen (False) -> kept True + violation
        layers = [
            ("program", Policy("git", "standard", {"force_push_forbidden": True})),
            ("agent", Policy("git", "a", {"force_push_forbidden": False})),
        ]
        eff, viol = compose(layers, {"force_push_forbidden"})
        assert eff.rules["force_push_forbidden"] is True
        assert len(viol) == 1
        v = viol[0]
        assert isinstance(v, LooseningViolation)
        assert v.rule == "force_push_forbidden" and v.layer == "agent"
        assert v.attempted is False and v.kept is True

    def test_agent_cannot_loosen_agent_merge_rule(self):
        # spec scenario: program forbids agents merging human branches; agent tries to allow
        rule = "agents_merge_human_owned_branch_forbidden"
        layers = [
            ("program", Policy("git", "standard", {rule: True})),
            ("agent", Policy("git", "loose", {rule: False})),
        ]
        eff, viol = compose(layers, {rule})
        assert eff.rules[rule] is True
        assert viol and viol[0].rule == rule

    def test_non_narrow_only_nearest_wins(self):
        layers = [
            ("program", Policy("git", "standard", {"merge_policy": "squash"})),
            ("agent", Policy("git", "a", {"merge_policy": "rebase"})),
        ]
        eff, viol = compose(layers, {"force_push_forbidden"})
        assert eff.rules["merge_policy"] == "rebase"  # nearest layer wins
        assert viol == []

    def test_non_bool_narrow_only_raises(self):
        layers = [("program", Policy("git", "s", {"force_push_forbidden": "yes"}))]
        with pytest.raises(PolicyError, match="boolean"):
            compose(layers, {"force_push_forbidden"})

    def test_empty_layers(self):
        eff, viol = compose([], {"x"})
        assert eff.rules == {} and viol == []


class TestSelection:
    def _cfg(self):
        return {
            "policies": {"git": "standard"},
            "repos": [
                {"name": "repo-strict", "policies": {"git": "owner-gated-strict"}},
                {"name": "repo-plain"},
            ],
            "agents": [{"name": "cli-agent", "policies": {"git": "agent-tight"}}],
        }

    def test_program_default(self):
        assert select_policy_names(self._cfg(), "git") == [("program", "standard")]

    def test_absent_selection_is_standard(self):
        assert select_policy_names({}, "git") == [("program", DEFAULT_POLICY_NAME)]

    def test_repo_override_adds_layer(self):
        sel = select_policy_names(self._cfg(), "git", repo="repo-strict")
        assert sel == [("program", "standard"), ("repo", "owner-gated-strict")]

    def test_repo_without_override_stays_program(self):
        assert select_policy_names(self._cfg(), "git", repo="repo-plain") == [
            ("program", "standard")
        ]

    def test_agent_override_adds_layer(self):
        sel = select_policy_names(self._cfg(), "git", agent="cli-agent")
        assert sel == [("program", "standard"), ("agent", "agent-tight")]


class TestEffectivePolicy:
    def test_standard_fallback_when_file_absent(self, tmp_path):
        # only `policies: {git: standard}`, no policy files on disk -> shipped standard
        eff, viol = effective_policy(tmp_path, {"policies": {"git": "standard"}}, "git")
        assert eff.rules["force_push_forbidden"] is True
        assert viol == []

    def test_repo_override_selects_stricter(self, tmp_path):
        # spec scenario: repo override composes on top of program standard
        _write_index(tmp_path, {"git": {"narrow_only": list(GIT_PACK_NARROW_ONLY)}})
        _write_policy(tmp_path, "git", "standard", dict.fromkeys(GIT_PACK_NARROW_ONLY, True))
        _write_policy(tmp_path, "git", "strict", {"owner_admission_required": True, "extra": 1})
        cfg = {
            "policies": {"git": "standard"},
            "repos": [{"name": "r", "policies": {"git": "strict"}}],
        }
        eff, viol = effective_policy(tmp_path, cfg, "git", repo="r")
        assert eff.rules["owner_admission_required"] is True
        assert eff.rules["extra"] == 1

    def test_missing_nonstandard_policy_raises(self, tmp_path):
        cfg = {"policies": {"git": "ghost"}}
        with pytest.raises(PolicyError, match="not found"):
            effective_policy(tmp_path, cfg, "git")

    def test_loosening_violation_surfaced_end_to_end(self, tmp_path):
        _write_index(tmp_path, {"git": {"narrow_only": ["force_push_forbidden"]}})
        _write_policy(tmp_path, "git", "standard", {"force_push_forbidden": True})
        _write_policy(tmp_path, "git", "loose", {"force_push_forbidden": False})
        cfg = {
            "policies": {"git": "standard"},
            "agents": [{"name": "a", "policies": {"git": "loose"}}],
        }
        eff, viol = effective_policy(tmp_path, cfg, "git", agent="a")
        assert eff.rules["force_push_forbidden"] is True
        assert viol and viol[0].layer == "agent"


class TestShippedStandard:
    def test_git_standard_forbids_force_push(self):
        p = shipped_standard("git")
        assert p.rules["force_push_forbidden"] is True
        assert p.rules["owner_admission_required"] is True
        assert p.rules["agents_merge_human_owned_branch_forbidden"] is True
        assert p.rules["agent_self_merge_on_owned_repo"] is True

    def test_git_standard_requires_status_checks_narrow_only(self):
        # D4a: policy asserts CI-must-be-required (intent); the check NAME is
        # resolved per-repo at generation, never a constant here.
        p = shipped_standard("git")
        assert p.rules["require_status_checks"] is True
        assert "require_status_checks" in GIT_PACK_NARROW_ONLY
        # no constant check-context name leaks into the policy
        assert "ci-ok" not in p.rules.values() and "lint-and-test" not in p.rules.values()

    def test_require_status_checks_cannot_be_loosened(self):
        # narrow-only: an agent layer setting it False is refused
        layers = [
            ("program", Policy("git", "standard", {"require_status_checks": True})),
            ("agent", Policy("git", "loose", {"require_status_checks": False})),
        ]
        eff, viol = compose(layers, GIT_PACK_NARROW_ONLY)
        assert eff.rules["require_status_checks"] is True
        assert any(v.rule == "require_status_checks" for v in viol)

    def test_unknown_pack_raises(self):
        with pytest.raises(PolicyError):
            shipped_standard("nope")

    def test_shipped_index_registers_git(self):
        idx = shipped_index()
        assert idx.narrow_only("git") == GIT_PACK_NARROW_ONLY


class TestBranchOwner:
    def test_convention(self):
        assert resolve_branch_owner("feat/roman/billing") == "roman"

    def test_registry_wins(self):
        assert resolve_branch_owner("weird-branch", branch_owners={"weird-branch": "ana"}) == "ana"

    def test_default_branch_uses_repo_owner(self):
        assert (
            resolve_branch_owner("main", repo_owner="cli-agent", is_default_branch=True)
            == "cli-agent"
        )

    def test_owner_less_returns_none(self):
        assert resolve_branch_owner("random") is None
        assert resolve_branch_owner("main", repo_owner="x", is_default_branch=False) is None


class TestDoctor:
    def test_drift_error_in_block_mode(self):
        eff = EffectivePolicy("git", {"force_push_forbidden": True})
        findings = check_policy_drift(
            eff, {"force_push_forbidden": False}, mode="block", repo="otaman-core"
        )
        assert [f.level for f in findings] == ["error"]
        assert "otaman-core" in findings[0].message and "otaman policy apply" in findings[0].message

    def test_drift_warn_in_warn_mode(self):
        eff = EffectivePolicy("git", {"force_push_forbidden": True})
        findings = check_policy_drift(eff, {"force_push_forbidden": False}, mode="warn")
        assert [f.level for f in findings] == ["warn"]

    def test_no_drift_when_live_matches(self):
        eff = EffectivePolicy("git", {"force_push_forbidden": True})
        assert check_policy_drift(eff, {"force_push_forbidden": True}, mode="block") == []

    def test_none_rules_skipped(self):
        eff = EffectivePolicy("git", {"branching": None})
        assert check_policy_drift(eff, {}, mode="block") == []

    def test_owner_less_branches_flagged(self):
        findings = check_owner_less_branches(
            ["feat/roman/x", "orphan", "main"],
            repo_owner="cli-agent",
            default_branch="main",
        )
        # feat/roman/x resolves (roman); main resolves (repo owner); orphan does not
        assert [f.level for f in findings] == ["warn"]
        assert "orphan" in findings[0].message

    def test_deprecated_standards_warn(self):
        cfg = {"standards": {"git": {"branching": {}, "environments": []}}}
        findings = check_deprecated_standards(cfg)
        assert [f.level for f in findings] == ["warn"]
        assert "branching" in findings[0].message and "environments" in findings[0].message

    def test_no_deprecation_when_absent(self):
        assert check_deprecated_standards({"standards": {"git": {}}}) == []
        assert check_deprecated_standards({}) == []

    def test_findings_are_doctor_findings(self):
        eff = EffectivePolicy("git", {"force_push_forbidden": True})
        f = check_policy_drift(eff, {"force_push_forbidden": False})
        assert isinstance(f[0], DoctorFinding)
