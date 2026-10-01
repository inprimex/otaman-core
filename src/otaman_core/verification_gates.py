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
``primary``/``fallback`` policy + ``sensitivity-overrides`` + ``clearances``); sghc's
``security-gates`` block migrates here (csp 1.2), retiring its relocation note.

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
    """

    hook: str
    primary: str
    fallback: str | None = None
    sensitivity_overrides: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationGatesConfig:
    """Parsed ``verification-gates.yaml``: clearances + per-hook policies."""

    clearances: dict[str, tuple[str, ...]] = field(default_factory=dict)
    hooks: dict[str, HookPolicy] = field(default_factory=dict)

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

    clearances: dict[str, tuple[str, ...]] = {}
    raw_clear = data.get("clearances", {})
    if not isinstance(raw_clear, Mapping):
        raise VerificationGatesError("clearances must be a mapping")
    for agent, classes in raw_clear.items():
        clearances[agent] = _str_tuple(classes, f"clearances.{agent}")

    hooks: dict[str, HookPolicy] = {}
    raw_hooks = data.get("hooks", {})
    if not isinstance(raw_hooks, Mapping):
        raise VerificationGatesError("hooks must be a mapping")
    for hook, raw in raw_hooks.items():
        if not isinstance(raw, Mapping):
            raise VerificationGatesError(f"hooks.{hook} must be a mapping")
        stray = set(raw) - {"primary", "fallback", "sensitivity-overrides"}
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
        hooks[hook] = HookPolicy(
            hook=hook, primary=primary, fallback=fallback, sensitivity_overrides=overrides
        )
    return VerificationGatesConfig(clearances=clearances, hooks=hooks)


def _run_policy(
    policy: str, ctx: SelectionContext, config: VerificationGatesConfig
) -> tuple[str, ...]:
    """Run one policy over *ctx*, returning selected critics (order-stable, deduped)."""
    if policy == "sensitivity-scoped":
        if ctx.sensitivity is None:
            return ()
        cleared = config.cleared_for(ctx.sensitivity)
        picked = [a for a in ctx.candidates if a in cleared]
    elif policy == "stakeholder-affected":
        picked = [ctx.repo_owners[r] for r in ctx.affected_repos if r in ctx.repo_owners]
    elif policy == "consumer-chain":
        picked = list(ctx.consumers)
    elif policy == "role-based":
        picked = (
            [a for a, roles in ctx.agent_roles.items() if ctx.target_role in roles]
            if ctx.target_role is not None
            else []
        )
    else:  # pragma: no cover - _check_policy guards this at parse
        raise VerificationGatesError(f"unknown policy {policy!r}")
    out: list[str] = []
    for a in picked:
        if a not in out:
            out.append(a)
    return tuple(out)


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

    def _run(policy: str) -> tuple[str, ...]:
        nonlocal excluded
        picked = _run_policy(policy, ctx, config)
        if ctx.proposer is not None and ctx.proposer in picked:
            excluded = True
            picked = tuple(c for c in picked if c != ctx.proposer)
        return picked

    sensitivity_override = False
    policy = hp.primary
    if ctx.sensitivity is not None and ctx.sensitivity in hp.sensitivity_overrides:
        policy = hp.sensitivity_overrides[ctx.sensitivity]
        sensitivity_override = True

    critics = _run(policy)
    fell_back = False
    if not critics and hp.fallback is not None:
        policy = hp.fallback
        critics = _run(policy)
        fell_back = True

    dropped: tuple[str, ...] = ()
    if ctx.sensitivity is not None:
        cleared = config.cleared_for(ctx.sensitivity)
        kept = tuple(a for a in critics if a in cleared)
        dropped = tuple(a for a in critics if a not in cleared)
        critics = kept

    return SelectionResult(
        hook=hook,
        policy=policy,
        critics=critics,
        fell_back=fell_back,
        sensitivity_override=sensitivity_override,
        dropped_uncleared=dropped,
        excluded_proposer=excluded,
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
