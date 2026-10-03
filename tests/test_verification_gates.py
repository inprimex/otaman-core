"""critic-selection-policy 1.1 — the policy engine + verification-gates.yaml schema.

Pins the four named policies, per-hook primary/fallback + sensitivity overrides,
the clearance gate (sensitive content never reaches an uncleared critic, dropped
visibly), and that every result names the policy that fired.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    pytest.skip("PyYAML not installed", allow_module_level=True)

from otaman_core.verification_gates import (
    POLICIES,
    SelectionContext,
    VerificationGatesError,
    parse_verification_gates,
    select_critics,
)

_CONFIG_DATA = {
    "clearances": {"cofounder-agent": ["cofounder-only"], "cto-agent": ["cofounder-only"]},
    "roles": {"reviewer-agent": ["reviewer"]},  # csp 1.4: role-based resolvable from config
    "hooks": {
        "proposal": {
            "primary": "stakeholder-affected",
            "fallback": "role-based",
            "target-role": "reviewer",  # csp 1.6: role-based hook needs a target-role
            "sensitivity-overrides": {"cofounder-only": "sensitivity-scoped"},
        },
        "release": {"primary": "role-based", "target-role": "reviewer"},
    },
}


def _cfg():
    return parse_verification_gates(_CONFIG_DATA)


# --- parse -------------------------------------------------------------------


def test_policies_canon():
    assert POLICIES == (
        "sensitivity-scoped",
        "stakeholder-affected",
        "consumer-chain",
        "role-based",
    )


def test_parse_none_and_empty():
    assert parse_verification_gates(None).hooks == {}
    assert parse_verification_gates({}).clearances == {}


def test_parse_full():
    cfg = _cfg()
    assert cfg.clearances["cofounder-agent"] == ("cofounder-only",)
    assert cfg.hooks["proposal"].primary == "stakeholder-affected"
    assert cfg.hooks["proposal"].fallback == "role-based"
    assert cfg.hooks["proposal"].sensitivity_overrides == {"cofounder-only": "sensitivity-scoped"}
    assert cfg.cleared_for("cofounder-only") == frozenset({"cofounder-agent", "cto-agent"})


@pytest.mark.parametrize(
    "data",
    [
        {"hooks": {"h": {"primary": "guess-work"}}},  # unknown policy
        {"hooks": {"h": {"fallback": "role-based"}}},  # missing primary
        {"hooks": {"h": {"primary": "role-based", "stray": 1}}},  # unknown key
        {
            "hooks": {"h": {"primary": "role-based", "sensitivity-overrides": {"x": "nope"}}}
        },  # bad override
        {"clearances": {"a": "cofounder-only"}},  # clearances value not a list
        {"hooks": "nope"},  # hooks not a mapping
    ],
)
def test_parse_rejects_malformed(data):
    with pytest.raises(VerificationGatesError):
        parse_verification_gates(data)


# --- selection: the four policies -------------------------------------------


def test_stakeholder_affected_one_per_owner():
    ctx = SelectionContext(
        affected_repos=("core", "cli", "web"),
        repo_owners={"core": "core-agent", "cli": "cli-agent", "web": "web-agent"},
    )
    r = select_critics(_cfg(), "proposal", ctx)
    assert r.policy == "stakeholder-affected"
    assert set(r.critics) == {"core-agent", "cli-agent", "web-agent"}


def test_role_based_selects_by_role():
    ctx = SelectionContext(
        agent_roles={"a": ("reviewer",), "b": ("developer",), "c": ("reviewer",)},
        target_role="reviewer",
    )
    r = select_critics(_cfg(), "release", ctx)
    assert r.policy == "role-based"
    assert set(r.critics) == {"a", "c"}


def test_consumer_chain():
    from otaman_core.verification_gates import HookPolicy, VerificationGatesConfig

    cfg = VerificationGatesConfig(hooks={"h": HookPolicy(hook="h", primary="consumer-chain")})
    r = select_critics(cfg, "h", SelectionContext(consumers=("downstream-agent",)))
    assert r.policy == "consumer-chain"
    assert r.critics == ("downstream-agent",)


def test_fallback_runs_when_primary_empty():
    # proposal primary = stakeholder-affected, but no affected repos -> fallback role-based
    ctx = SelectionContext(agent_roles={"r": ("reviewer",)}, target_role="reviewer")
    r = select_critics(_cfg(), "proposal", ctx)
    assert r.fell_back is True
    assert r.policy == "role-based"
    assert r.critics == ("r",)


# --- csp 1.4: roles table, could-not-evaluate, parse-time refusal ------------


def test_roles_table_is_parsed_and_exposed():
    cfg = _cfg()
    assert cfg.roles == {"reviewer-agent": ("reviewer",)}


def test_role_based_resolves_from_config_roles_alone():
    # no ctx.agent_roles: role-based reads the config roles table (the 1.4 point)
    cfg = _cfg()
    r = select_critics(cfg, "release", SelectionContext(target_role="reviewer"))
    assert r.policy == "role-based"
    assert r.critics == ("reviewer-agent",)
    assert r.could_not_evaluate == ()


def test_could_not_evaluate_names_missing_inputs_distinct_from_no_eligible():
    from otaman_core.verification_gates import HookPolicy, VerificationGatesConfig

    # role-based primary, roles declared so parse passes, but the CALL supplies no
    # target_role and no roles override -> cannot evaluate, names the missing input.
    cfg = VerificationGatesConfig(
        roles={"reviewer-agent": ("reviewer",)},
        hooks={"h": HookPolicy(hook="h", primary="role-based")},
    )
    r = select_critics(cfg, "h", SelectionContext())  # no target_role
    assert r.critics == ()
    assert r.could_not_evaluate == ("target_role",)  # could-not-know, names the input

    # contrast: a no-eligible-critic — evaluated fine (roles + target_role present) but
    # nobody holds the role. Empty critics, but could_not_evaluate is EMPTY.
    r2 = select_critics(cfg, "h", SelectionContext(target_role="nonesuch"))
    assert r2.critics == () and r2.could_not_evaluate == ()


def test_could_not_evaluate_names_both_when_roles_and_role_absent():
    from otaman_core.verification_gates import HookPolicy, VerificationGatesConfig

    # built directly (no roles table) so the parse-time guard is bypassed; the engine
    # still reports both missing inputs when role-based runs with neither.
    cfg = VerificationGatesConfig(hooks={"h": HookPolicy(hook="h", primary="role-based")})
    r = select_critics(cfg, "h", SelectionContext())
    assert r.could_not_evaluate == ("roles", "target_role")


def test_exclusion_emptied_is_no_eligible_not_could_not_evaluate():
    # role-based evaluated fine and found the proposer; exclusion empties it -> that is a
    # no-eligible-critic, NOT a could-not-evaluate (it knew, the only critic was excluded).
    from otaman_core.verification_gates import HookPolicy, VerificationGatesConfig

    cfg = VerificationGatesConfig(
        roles={"core-agent": ("reviewer",)},
        hooks={"h": HookPolicy(hook="h", primary="role-based")},
    )
    r = select_critics(cfg, "h", SelectionContext(target_role="reviewer", proposer="core-agent"))
    assert r.critics == () and r.excluded_proposer is True
    assert r.could_not_evaluate == ()


def test_parse_refuses_pairing_that_cannot_select_self_owned():
    # stakeholder-affected with no fallback: the self-owned single-repo shape empties
    # under exclusion and nothing restores it -> refused at parse, naming the pairing.
    with pytest.raises(VerificationGatesError, match="single-repo proposal by its own owner"):
        parse_verification_gates(
            {"roles": {"r": ["reviewer"]}, "hooks": {"h": {"primary": "stakeholder-affected"}}}
        )
    # a consumer-chain fallback does not save it (consumers are runtime, not config)
    with pytest.raises(VerificationGatesError, match="single-repo proposal by its own owner"):
        parse_verification_gates(
            {
                "roles": {"r": ["reviewer"]},
                "hooks": {"h": {"primary": "stakeholder-affected", "fallback": "consumer-chain"}},
            }
        )


def test_parse_refuses_role_based_without_a_roles_table():
    with pytest.raises(VerificationGatesError, match="needs a roles table"):
        parse_verification_gates({"hooks": {"h": {"primary": "role-based"}}})


def test_parse_accepts_role_based_pairing_with_roles():
    cfg = parse_verification_gates(
        {
            "roles": {"reviewer-agent": ["reviewer"]},
            "hooks": {
                "h": {
                    "primary": "stakeholder-affected",
                    "fallback": "role-based",
                    "target-role": "reviewer",
                }
            },
        }
    )
    assert cfg.hooks["h"].fallback == "role-based"
    assert cfg.hooks["h"].target_role == "reviewer"


# --- csp 1.6: per-hook target-role in config ---------------------------------


def test_parse_refuses_role_based_hook_without_a_target_role():
    with pytest.raises(VerificationGatesError, match="needs a target-role"):
        parse_verification_gates(
            {"roles": {"a": ["reviewer"]}, "hooks": {"h": {"primary": "role-based"}}}
        )


def test_role_based_resolves_target_role_from_config_for_a_checkout():
    # the 1.6 point: a bare checkout supplies NO ctx.target_role; the hook's configured
    # target-role resolves role-based, so it evaluates instead of could-not-evaluate.
    cfg = parse_verification_gates(
        {
            "roles": {"reviewer-agent": ["reviewer"]},
            "hooks": {"release": {"primary": "role-based", "target-role": "reviewer"}},
        }
    )
    r = select_critics(cfg, "release", SelectionContext())  # no ctx.target_role at all
    assert r.policy == "role-based"
    assert r.critics == ("reviewer-agent",)
    assert r.could_not_evaluate == ()


def test_caller_target_role_overrides_the_configured_default():
    cfg = parse_verification_gates(
        {
            "roles": {"reviewer-agent": ["reviewer"], "auditor-agent": ["auditor"]},
            "hooks": {"release": {"primary": "role-based", "target-role": "reviewer"}},
        }
    )
    r = select_critics(cfg, "release", SelectionContext(target_role="auditor"))
    assert r.critics == ("auditor-agent",)  # caller wins over the hook default


def test_parse_refuses_malformed_target_role():
    with pytest.raises(VerificationGatesError, match="target-role"):
        parse_verification_gates(
            {
                "roles": {"a": ["reviewer"]},
                "hooks": {"h": {"primary": "role-based", "target-role": ""}},
            }
        )


def test_parse_refuses_unknown_top_level_key():
    # cli's free finding: an unknown TOP-LEVEL key was silently dropped, so a tenant's
    # roles table (pre-1.4 guess) vanished with no error. Now refused, like a hook stray.
    with pytest.raises(VerificationGatesError, match="unknown top-level key"):
        parse_verification_gates({"rolez": {"a": ["reviewer"]}})


def test_parse_refuses_malformed_roles_table():
    with pytest.raises(VerificationGatesError, match="roles"):
        parse_verification_gates({"roles": {"a": "reviewer"}})  # value must be a list


# --- sensitivity override + clearance gate ----------------------------------


def test_sensitivity_override_switches_policy():
    ctx = SelectionContext(sensitivity="cofounder-only", candidates=("cofounder-agent",))
    r = select_critics(_cfg(), "proposal", ctx)
    assert r.sensitivity_override is True
    assert r.policy == "sensitivity-scoped"
    assert r.critics == ("cofounder-agent",)


def test_clearance_gate_drops_uncleared_whatever_the_policy():
    # stakeholder-affected on cofounder-only content would pick an uncleared owner;
    # the clearance gate drops it (sensitive content never reaches an uncleared critic)
    from otaman_core.verification_gates import HookPolicy, VerificationGatesConfig

    cfg = VerificationGatesConfig(
        clearances={"cofounder-agent": ("cofounder-only",)},
        hooks={"h": HookPolicy(hook="h", primary="stakeholder-affected")},
    )
    ctx = SelectionContext(
        sensitivity="cofounder-only",
        affected_repos=("core", "secret"),
        repo_owners={"core": "core-agent", "secret": "cofounder-agent"},
    )
    r = select_critics(cfg, "h", ctx)
    assert r.critics == ("cofounder-agent",)  # cleared one kept
    assert r.dropped_uncleared == ("core-agent",)  # uncleared one dropped, visibly


def test_unknown_hook_refuses():
    with pytest.raises(VerificationGatesError):
        select_critics(_cfg(), "no-such-hook", SelectionContext())


# --- schema file -------------------------------------------------------------


def test_schema_is_valid_and_accepts_the_example():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "otaman_core"
        / "schemas"
        / "verification-gates-schema.yaml"
    )
    with open(schema_path, encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    jsonschema.Draft7Validator.check_schema(schema)
    v = jsonschema.Draft7Validator(schema)
    assert list(v.iter_errors(_CONFIG_DATA)) == []
    # an unknown policy is rejected by the schema too
    assert list(v.iter_errors({"hooks": {"h": {"primary": "nope"}}}))


# --- D4 independence invariant: proposer never self-reviews (ruling 20261001T205124) ---


def test_proposer_excluded_from_selection():
    ctx = SelectionContext(
        affected_repos=("core", "cli"),
        repo_owners={"core": "core-agent", "cli": "cli-agent"},
        proposer="core-agent",  # proposer owns an affected repo
    )
    r = select_critics(_cfg(), "proposal", ctx)
    assert "core-agent" not in r.critics  # never reviews own proposal
    assert r.critics == ("cli-agent",)
    assert r.excluded_proposer is True


def test_exclusion_emptying_primary_triggers_fallback():
    # proposer is the ONLY stakeholder -> primary empties -> fallback (role-based) runs
    ctx = SelectionContext(
        affected_repos=("core",),
        repo_owners={"core": "core-agent"},
        proposer="core-agent",
        agent_roles={"reviewer-agent": ("reviewer",)},
        target_role="reviewer",
    )
    r = select_critics(_cfg(), "proposal", ctx)
    assert r.fell_back is True
    assert r.policy == "role-based"
    assert r.critics == ("reviewer-agent",)
    assert r.excluded_proposer is True


def test_no_proposer_means_no_exclusion():
    ctx = SelectionContext(affected_repos=("core",), repo_owners={"core": "core-agent"})
    r = select_critics(_cfg(), "proposal", ctx)
    assert r.critics == ("core-agent",)
    assert r.excluded_proposer is False


def test_proposer_also_excluded_from_fallback():
    # proposer would be selected by BOTH primary and fallback -> excluded from both
    ctx = SelectionContext(
        affected_repos=("core",),
        repo_owners={"core": "core-agent"},
        proposer="core-agent",
        agent_roles={"core-agent": ("reviewer",)},  # proposer also has the fallback role
        target_role="reviewer",
    )
    r = select_critics(_cfg(), "proposal", ctx)
    assert "core-agent" not in r.critics
    assert r.critics == ()  # both policies emptied by exclusion
    assert r.excluded_proposer is True
