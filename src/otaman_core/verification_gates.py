"""Critic-selection policy engine + ``verification-gates.yaml`` schema (csp 1.1).

Verification gates resolve WHICH critics review a thing through one of four named
policies, configured per hook. This module is the single home (two-consumer rule)
for that engine and its config, consumed by the plugin dispatch (JTBD-57's critic
picker migrates onto these — csp 1.2) and the cli config surface + doctor (1.3).

The four policies (proposal §What-changes):

- ``sensitivity-scoped`` — the critic must DECLARE matching clearance for the
  content's sensitivity class. The privacy policy: sensitive material only reaches
  cleared critics.
- ``stakeholder-affected`` — one critic per owner of each affected repo. Adversarial
  diversity falls out: every repo the change touches gets its owner as a reviewer.
- ``consumer-chain`` — the critic is a downstream consumer derived from the bus
  chain (the caller supplies the consumers).
- ``role-based`` — a named role's agent, regardless of ownership.

``verification-gates.yaml`` is the single home for gate configuration (per-hook
``primary``/``fallback`` policy + ``sensitivity-overrides`` + ``clearances`` + a
``roles`` table); sghc's ``security-gates`` block migrates here (csp 1.2), retiring its
relocation note.

Coverage is configuration-complete (csp 1.4/1.6): the ``roles`` table (agent -> roles) and
a per-hook ``target_role`` make ``role-based`` resolvable from config alone — both inputs
the parse can see — so it is a usable fallback for the self-owned single-repo shape where
exclusion empties ``stakeholder-affected``, and resolvable from a bare checkout (no
caller). A hook naming ``role-based`` with no roles table or no ``target_role`` is refused
at parse time, as is a pairing that cannot select for that shape; and a policy that cannot
evaluate for want of a declared input reports ``could_not_evaluate`` (the missing input
names), distinct from an evaluated-but-empty no-eligible-critic.

Two invariants the engine enforces, both no-silent-success:

- **clearance gate** — whatever policy fires, content carrying a sensitivity class
  only reaches critics that declare matching clearance. The policy selects; this
  filters. A policy that selects an uncleared agent for sensitive content does not
  leak it — the agent is dropped and the result says so.
- **named selection** — every :class:`SelectionResult` records the policy that chose
  its critics, so coverage is auditable from the record.

The cofounder 4-layer stack (deterministic / self-critique / cleared peer / human,
with not-run rendering) is assembled by the plugin dispatch (csp 1.2) FROM these
primitives; core provides the policies + the clearance filter, not the stack.

Pure: config parsing + selection over caller-supplied context. No dispatch, no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The four critic-selection policies (proposal). ``primary``/``fallback`` and every
#: ``sensitivity-overrides`` value must name one of these.
POLICIES: tuple[str, ...] = (
    "sensitivity-scoped",
    "stakeholder-affected",
    "consumer-chain",
    "role-based",
)


class VerificationGatesError(ValueError):
    """A malformed ``verification-gates.yaml`` — unknown policy, bad shape."""


@dataclass(frozen=True)
class HookPolicy:
    """A hook's configured selection: a primary policy, optional fallback, overrides.

    ``sensitivity_overrides`` maps a sensitivity class to the policy that replaces
    ``primary`` when the content carries that class (e.g. ``cofounder-only`` ->
    ``sensitivity-scoped``).

    ``target_role`` is the per-hook default role ``role-based`` selects by (csp 1.6).
    It lives in config, not per-dispatcher, so it is the one role-based input the parse
    CAN see — a role-based hook with no ``target_role`` is refused at parse time, and
    :func:`select_critics` resolves it from here when the caller supplies none, so a
    checkout with a complete config resolves role-based instead of returning
    could-not-evaluate on every call (ruled 2026-10-03; cli 20261003T064521).
    """

    hook: str
    primary: str
    fallback: str | None = None
    sensitivity_overrides: Mapping[str, str] = field(default_factory=dict)
    target_role: str | None = None


@dataclass(frozen=True)
class VerificationGatesConfig:
    """Parsed ``verification-gates.yaml``: clearances + per-hook policies + roles.

    ``roles`` is the agent -> roles table (csp 1.4) that makes ``role-based`` resolvable
    from CONFIGURATION ALONE — the load-bearing fallback for the self-owned single-repo
    shape, where the proposer owns the one affected repo and is excluded, so
    ``stakeholder-affected`` empties and only a config-declared role can name an
    independent critic. Before 1.4 the roles lived only in the caller-supplied
    :class:`SelectionContext`, so a checkout could not resolve ``role-based`` and the
    doctor's roster view had nothing to show (cli 20261003T040454).
    """

    clearances: dict[str, tuple[str, ...]] = field(default_factory=dict)
    hooks: dict[str, HookPolicy] = field(default_factory=dict)
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def cleared_for(self, sensitivity: str) -> frozenset[str]:
        """Agents that declare clearance for *sensitivity*."""
        return frozenset(a for a, classes in self.clearances.items() if sensitivity in classes)


@dataclass(frozen=True)
class SelectionContext:
    """The inputs the policies select over — supplied by the caller (dispatch).

    Each policy reads only the fields it needs: ``sensitivity-scoped`` uses
    ``candidates`` + the config's clearances; ``stakeholder-affected`` uses
    ``affected_repos`` + ``repo_owners``; ``consumer-chain`` uses ``consumers``;
    ``role-based`` uses ``agent_roles`` + ``target_role``.
    """

    candidates: tuple[str, ...] = ()
    sensitivity: str | None = None
    affected_repos: tuple[str, ...] = ()
    repo_owners: Mapping[str, str] = field(default_factory=dict)
    consumers: tuple[str, ...] = ()
    agent_roles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    target_role: str | None = None
    #: The proposing agent, excluded from every policy's critic set — a proposer never
    #: reviews their own proposal (the D4 independence INVARIANT, spec-agent ruling
    #: 20261001T205124). ``None`` when there is no proposer to exclude.
    proposer: str | None = None


@dataclass(frozen=True)
class SelectionResult:
    """The chosen critics and the policy that chose them (the recorded fact).

    ``fell_back`` marks that ``primary`` selected nothing and ``fallback`` ran;
    ``sensitivity_override`` marks that a sensitivity class replaced ``primary``;
    ``dropped_uncleared`` names critics the policy picked but the clearance gate
    removed because the content was sensitive and they declared no matching
    clearance — visible, never silent.
    """

    hook: str
    policy: str
    critics: tuple[str, ...]
    fell_back: bool = False
    sensitivity_override: bool = False
    dropped_uncleared: tuple[str, ...] = ()
    #: True when the proposer was removed from a policy's selection (D4 invariant).
    excluded_proposer: bool = False
    #: The names of the REQUIRED inputs that were undeclared, when the final policy could
    #: not be evaluated for want of them (csp 1.4) — e.g. ``("roles", "target_role")`` for
    #: a ``role-based`` selection with no roles table and no target role. EMPTY means the
    #: policy WAS evaluated: an empty ``critics`` with empty ``could_not_evaluate`` is a
    #: real no-eligible-critic ("evaluated, selected nobody"), which the surface must
    #: render differently from "could not know, missing <input>" (cli 20261003T044540 —
    #: a non-empty tuple, not a bool, so the surface names the input without a second
    #: policy->inputs table).
    could_not_evaluate: tuple[str, ...] = ()


def _str_tuple(value: Any, where: str) -> tuple[str, ...]:
    if not (isinstance(value, list) and all(isinstance(x, str) for x in value)):
        raise VerificationGatesError(f"{where} must be a list of strings")
    return tuple(value)


def _check_policy(name: Any, where: str) -> str:
    if name not in POLICIES:
        raise VerificationGatesError(
            f"{where}: unknown policy {name!r}; one of {', '.join(POLICIES)}"
        )
    return name


def parse_verification_gates(data: Any) -> VerificationGatesConfig:
    """Parse and validate ``verification-gates.yaml`` (``None``/absent -> empty).

    Validates that every ``primary``/``fallback``/override value names a policy in
    :data:`POLICIES`, that clearances map agents to class lists, and that hook
    entries are well-shaped. Raises :class:`VerificationGatesError` loudly on the
    first malformation — gate config must not resolve a typo to a silent default.
    """
    if data is None:
        return VerificationGatesConfig()
    if not isinstance(data, Mapping):
        raise VerificationGatesError("verification-gates must be a mapping")

    # Refuse unknown TOP-LEVEL keys, as a hook already refuses unknown inner keys. Without
    # this a tenant's ``roles:`` (or a typo'd ``clearances``) parsed clean and VANISHED —
    # the config said one thing, the engine saw another (cli 20261003T044540). Especially
    # load-bearing now that ``roles:`` is a real key a tenant will reach for.
    stray_top = set(data) - {"clearances", "hooks", "roles"}
    if stray_top:
        raise VerificationGatesError(f"unknown top-level key(s) {sorted(stray_top)!r}")

    clearances: dict[str, tuple[str, ...]] = {}
    raw_clear = data.get("clearances", {})
    if not isinstance(raw_clear, Mapping):
        raise VerificationGatesError("clearances must be a mapping")
    for agent, classes in raw_clear.items():
        clearances[agent] = _str_tuple(classes, f"clearances.{agent}")

    roles: dict[str, tuple[str, ...]] = {}
    raw_roles = data.get("roles", {})
    if not isinstance(raw_roles, Mapping):
        raise VerificationGatesError("roles must be a mapping (agent -> list of roles)")
    for agent, agent_roles in raw_roles.items():
        roles[agent] = _str_tuple(agent_roles, f"roles.{agent}")

    hooks: dict[str, HookPolicy] = {}
    raw_hooks = data.get("hooks", {})
    if not isinstance(raw_hooks, Mapping):
        raise VerificationGatesError("hooks must be a mapping")
    for hook, raw in raw_hooks.items():
        if not isinstance(raw, Mapping):
            raise VerificationGatesError(f"hooks.{hook} must be a mapping")
        stray = set(raw) - {"primary", "fallback", "sensitivity-overrides", "target-role"}
        if stray:
            raise VerificationGatesError(f"hooks.{hook}: unknown key(s) {sorted(stray)!r}")
        if "primary" not in raw:
            raise VerificationGatesError(f"hooks.{hook}: primary is required")
        primary = _check_policy(raw["primary"], f"hooks.{hook}.primary")
        fallback = None
        if raw.get("fallback") is not None:
            fallback = _check_policy(raw["fallback"], f"hooks.{hook}.fallback")
        overrides: dict[str, str] = {}
        raw_over = raw.get("sensitivity-overrides", {})
        if not isinstance(raw_over, Mapping):
            raise VerificationGatesError(f"hooks.{hook}.sensitivity-overrides must be a mapping")
        for sens, pol in raw_over.items():
            overrides[sens] = _check_policy(pol, f"hooks.{hook}.sensitivity-overrides.{sens}")
        target_role = raw.get("target-role")
        if target_role is not None and not (isinstance(target_role, str) and target_role.strip()):
            raise VerificationGatesError(f"hooks.{hook}.target-role must be a non-empty string")
        hooks[hook] = HookPolicy(
            hook=hook,
            primary=primary,
            fallback=fallback,
            sensitivity_overrides=overrides,
            target_role=target_role,
        )

    # Configuration-completeness (csp 1.4): every hook's primary/fallback pairing must be
    # able to select a critic for the fleet's most common proposal shape — a single-repo
    # proposal by the repo's own owner. Under the D4 exclusion invariant the proposer is
    # removed, so ``stakeholder-affected`` (which only ever names repo owners) empties, and
    # ``consumer-chain`` / ``sensitivity-scoped`` depend on runtime context, not config.
    # The one policy that can name an INDEPENDENT critic from configuration alone is
    # ``role-based`` with a declared roles table. A pairing that lacks it cannot select for
    # that shape and is refused at parse time, naming the pairing (measured 2026-10-03: no
    # fallback restored the shape; role-based read context fields no file declared).
    has_roles = bool(roles)
    for hook, hp in hooks.items():
        pairing = [hp.primary] + ([hp.fallback] if hp.fallback is not None else [])
        pairing_str = "/".join(pairing)
        if "role-based" not in pairing:
            raise VerificationGatesError(
                f"hooks.{hook}: the primary/fallback pairing ({pairing_str}) cannot select a "
                "critic for a single-repo proposal by its own owner — the proposer is excluded "
                "(D4) and only role-based resolves an independent critic from config alone; "
                "add role-based as primary or fallback"
            )
        if not has_roles:
            raise VerificationGatesError(
                f"hooks.{hook}: role-based ({pairing_str}) needs a roles table, but none is "
                "declared — add a top-level roles: (agent -> roles) so role-based resolves "
                "from configuration alone"
            )
        # target_role is the other role-based input the parse can see (csp 1.6): a hook
        # naming role-based with no declared target-role would could-not-evaluate on every
        # call, so refuse it here the same way a missing roles table is refused.
        if hp.target_role is None:
            raise VerificationGatesError(
                f"hooks.{hook}: role-based ({pairing_str}) needs a target-role, but none is "
                "declared — add target-role: to the hook so role-based resolves from config "
                "(not per dispatcher)"
            )

    return VerificationGatesConfig(clearances=clearances, hooks=hooks, roles=roles)


def _run_policy(
    policy: str,
    ctx: SelectionContext,
    config: VerificationGatesConfig,
    target_role: str | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Run one policy over *ctx*; return ``(critics, missing_inputs)``.

    ``critics`` is order-stable and deduped. ``missing_inputs`` is non-empty only when the
    policy could NOT be evaluated for want of a required DECLARED input (csp 1.4) — today
    only ``role-based``, whose inputs are the roles table (config ``roles:`` or the
    caller's ``agent_roles``) and a ``target_role``. An evaluated policy that simply
    selects nobody returns ``((), ())`` — a no-eligible-critic, not a could-not-evaluate.

    *target_role* is the already-resolved role (caller's ``ctx.target_role`` or the hook's
    configured ``target_role`` — csp 1.6), passed in so role-based resolves from config
    alone when the caller supplies none.
    """
    missing: tuple[str, ...] = ()
    if policy == "sensitivity-scoped":
        if ctx.sensitivity is None:
            return (), ()
        cleared = config.cleared_for(ctx.sensitivity)
        picked = [a for a in ctx.candidates if a in cleared]
    elif policy == "stakeholder-affected":
        picked = [ctx.repo_owners[r] for r in ctx.affected_repos if r in ctx.repo_owners]
    elif policy == "consumer-chain":
        picked = list(ctx.consumers)
    elif policy == "role-based":
        # resolvable from CONFIG ALONE: the caller's agent_roles override when supplied,
        # else the config roles table; likewise *target_role* is ctx-or-config (csp 1.6).
        # Report undeclared inputs rather than silently selecting nobody (cli's #275 bug:
        # "selected nobody" asserted an emptiness it could not know).
        effective_roles = ctx.agent_roles or config.roles
        absent = []
        if not effective_roles:
            absent.append("roles")
        if target_role is None:
            absent.append("target_role")
        if absent:
            return (), tuple(absent)
        picked = [a for a, roles in effective_roles.items() if target_role in roles]
    else:  # pragma: no cover - _check_policy guards this at parse
        raise VerificationGatesError(f"unknown policy {policy!r}")
    out: list[str] = []
    for a in picked:
        if a not in out:
            out.append(a)
    return tuple(out), missing


def select_critics(
    config: VerificationGatesConfig, hook: str, ctx: SelectionContext
) -> SelectionResult:
    """Select critics for *hook* over *ctx*, honoring overrides, fallback, clearance.

    Resolution order:
    1. if the content's sensitivity class has an override, that policy replaces
       ``primary`` (``sensitivity_override`` set);
    2. run the chosen policy, then EXCLUDE the proposer (D4 independence invariant —
       no policy may select the proposer as a critic of their own proposal); if the
       result is empty (nothing selected, OR exclusion emptied it) and a ``fallback``
       is configured, run the fallback, also proposer-excluded (``fell_back`` set);
    3. the CLEARANCE GATE: if the content carries a sensitivity class, drop any
       selected critic that declares no matching clearance — sensitive content never
       reaches an uncleared critic, whatever policy chose it (the dropped agents are
       recorded in ``dropped_uncleared``, never silently removed).

    Every result names the policy that fired. An unknown hook raises — a gate with no
    configured policy is a config gap, not a silent "no critics".
    """
    hp = config.hooks.get(hook)
    if hp is None:
        raise VerificationGatesError(f"no policy configured for hook {hook!r}")

    excluded = False
    # role-based resolves its role from the caller, else the hook's configured default
    # (csp 1.6) — so a checkout with a complete config resolves role-based with no caller.
    target_role = ctx.target_role or hp.target_role

    def _run(policy: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        nonlocal excluded
        picked, missing = _run_policy(policy, ctx, config, target_role)
        if ctx.proposer is not None and ctx.proposer in picked:
            excluded = True
            picked = tuple(c for c in picked if c != ctx.proposer)
        return picked, missing

    sensitivity_override = False
    policy = hp.primary
    if ctx.sensitivity is not None and ctx.sensitivity in hp.sensitivity_overrides:
        policy = hp.sensitivity_overrides[ctx.sensitivity]
        sensitivity_override = True

    critics, missing = _run(policy)
    fell_back = False
    if not critics and hp.fallback is not None:
        policy = hp.fallback
        critics, missing = _run(policy)  # missing now reflects the final (fallback) policy
        fell_back = True

    dropped: tuple[str, ...] = ()
    if ctx.sensitivity is not None:
        cleared = config.cleared_for(ctx.sensitivity)
        kept = tuple(a for a in critics if a in cleared)
        dropped = tuple(a for a in critics if a not in cleared)
        critics = kept

    # could-not-evaluate only when the final policy left no critics BECAUSE a required
    # input was undeclared — not when it evaluated and found nobody, and not when the
    # exclusion invariant emptied a real selection (that is a no-eligible-critic).
    could_not_evaluate = missing if not critics else ()

    return SelectionResult(
        hook=hook,
        policy=policy,
        critics=critics,
        fell_back=fell_back,
        sensitivity_override=sensitivity_override,
        dropped_uncleared=dropped,
        excluded_proposer=excluded,
        could_not_evaluate=could_not_evaluate,
    )


__all__ = [
    "POLICIES",
    "HookPolicy",
    "SelectionContext",
    "SelectionResult",
    "VerificationGatesConfig",
    "VerificationGatesError",
    "parse_verification_gates",
    "select_critics",
]
