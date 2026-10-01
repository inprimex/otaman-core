"""The ``security-gates:`` config schema — Hook C's cost-stratified ladder (sghc 1.1).

The pre-PR security gate (Hook C) runs five layers in COST order, cheap and
deterministic first, the LLM observer last and scoped to what the deterministic
layers could not decide (design D1). This module is the single home (two-consumer
rule) for the config model that names those layers and their tools, consumed by the
cli gate-evaluation + doctor (sghc 1.2), the deploy CI templates (1.3), and the
plugin per-repo CI generator (1.4).

The five layers, in order (cost ascending; the first three block, the last two are
advisory):

1. ``pre-commit``   — local: lint/format/types/local secrets.
2. ``ci-fast``      — <30s, blocking: history secrets, language SAST, dep audit.
3. ``ci-medium``    — <5min, blocking: full SAST + exactly TWO of trivy/grype/
   osv-scanner (the chosen pair is recorded so the omission is visible, D1) +
   licensee.
4. ``ci-slow``      — advisory, opt-in per repo: deep SAST, verified secrets, etc.
5. ``llm-observer`` — advisory, per-PR cost cap; reads ONLY residual (design D2,
   enforced at plugin's input seam, not here).

Config lives in ``platform.yaml`` under ``security-gates:`` today (design D4);
:data:`RELOCATION_NOTE` records that JTBD-59's ``verification-gates.yaml`` is the
anticipated home — one home at a time, so this block carries the note rather than
being declared in two files.

Resolution (see :func:`resolve_repo_gates`): per-language defaults, unioned for a
mixed-language repo, with per-repo overrides and a per-repo opt-out. This module is
pure: it models and resolves config; it neither runs tools nor makes policy calls
(cli 1.2 evaluates verdicts over the resolved gates).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The five layers, in cost order (design D1). The order is canonical — consumers
#: iterate a resolved repo's layers in this sequence.
LAYERS: tuple[str, ...] = ("pre-commit", "ci-fast", "ci-medium", "ci-slow", "llm-observer")

#: Default blocking per layer (spec): the first three block, the last two are
#: advisory (ci-slow is opt-in, llm-observer never blocks — design D3). A config may
#: override ``blocking`` per layer; these are the fallbacks when it does not.
_DEFAULT_BLOCKING: dict[str, bool] = {
    "pre-commit": True,
    "ci-fast": True,
    "ci-medium": True,
    "ci-slow": False,
    "llm-observer": False,
}

#: The three interchangeable dependency scanners; ci-medium records EXACTLY two of
#: them (research-measured overlap, not preference — design D1). The recorded pair
#: makes the omitted third visible, per the debt-register convention.
SCANNERS: tuple[str, ...] = ("trivy", "grype", "osv-scanner")

#: How many scanners ci-medium records (the "2-of-3" rule).
SCANNER_PAIR_SIZE = 2

#: The layer that records a scanner pair.
SCANNER_LAYER = "ci-medium"

#: The advisory layer a repo opts INTO (spec: "ci-slow — advisory, opt-in per repo").
#: ``opt-in`` is accepted only here, and only on a repo entry — never a language
#: default (opt-in is a per-repo decision, not a language-wide one).
OPT_IN_LAYER = "ci-slow"

#: JTBD-59 relocation note (design D4). The block lives in platform.yaml now;
#: verification-gates.yaml is its anticipated home. Declaring both would be the
#: two-files-drift class, so the block stays single-homed and carries this note.
RELOCATION_NOTE = (
    "security-gates lives in platform.yaml today; its anticipated home is "
    "verification-gates.yaml under JTBD-59. One home at a time — do not declare both."
)

_LAYER_FIELDS = frozenset({"tools", "blocking", "timeout", "scanner-pair", "opt-in", "cost-cap"})


class SecurityGatesError(ValueError):
    """A malformed ``security-gates:`` block — bad layer, scanner pair, or type."""


@dataclass(frozen=True)
class LayerGate:
    """One resolved layer of the ladder for a repo.

    ``scanner_pair`` is populated only for :data:`SCANNER_LAYER`; ``opt_in`` marks a
    ci-slow layer a repo has opted into; ``cost_cap`` bounds the llm-observer per PR.
    """

    layer: str
    tools: tuple[str, ...] = ()
    blocking: bool = False
    timeout: int | None = None
    scanner_pair: tuple[str, ...] = ()
    opt_in: bool = False
    cost_cap: float | None = None


@dataclass(frozen=True)
class RepoSecurityGates:
    """A repo's resolved gates. ``opt_out`` repos carry no layers — visibly skipped."""

    repo: str
    opt_out: bool = False
    languages: tuple[str, ...] = ()
    layers: tuple[LayerGate, ...] = ()

    def layer(self, name: str) -> LayerGate | None:
        """The resolved :class:`LayerGate` named *name*, or ``None`` if absent."""
        return next((la for la in self.layers if la.layer == name), None)


@dataclass(frozen=True)
class SecurityGatesConfig:
    """The parsed ``security-gates:`` block: per-language defaults + per-repo config."""

    languages: dict[str, dict[str, LayerGate]] = field(default_factory=dict)
    repos: dict[str, dict[str, Any]] = field(default_factory=dict)
    relocation_note: str = RELOCATION_NOTE


def _as_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SecurityGatesError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _parse_scanner_pair(raw: Any, where: str) -> tuple[str, ...]:
    """Validate a recorded scanner pair: EXACTLY two distinct members of SCANNERS."""
    if not (isinstance(raw, list) and all(isinstance(s, str) for s in raw)):
        raise SecurityGatesError(f"{where}.scanner-pair must be a list of scanner names")
    pair = tuple(raw)
    if len(set(pair)) != SCANNER_PAIR_SIZE or len(pair) != SCANNER_PAIR_SIZE:
        raise SecurityGatesError(
            f"{where}.scanner-pair must name exactly {SCANNER_PAIR_SIZE} distinct "
            f"scanners (the 2-of-3 rule), got {raw!r}"
        )
    unknown = [s for s in pair if s not in SCANNERS]
    if unknown:
        raise SecurityGatesError(
            f"{where}.scanner-pair has unknown scanner(s) {unknown!r}; "
            f"choose two of {', '.join(SCANNERS)}"
        )
    return pair


def _parse_layer(name: str, raw: Any, where: str, *, allow_opt_in: bool) -> LayerGate:
    if name not in LAYERS:
        raise SecurityGatesError(f"{where}: unknown layer {name!r}; layers are {', '.join(LAYERS)}")
    body = _as_mapping(raw, f"{where}.{name}")
    stray = set(body) - _LAYER_FIELDS
    if stray:
        raise SecurityGatesError(f"{where}.{name}: unknown key(s) {sorted(stray)!r}")
    tools = body.get("tools", [])
    if not (isinstance(tools, list) and all(isinstance(t, str) for t in tools)):
        raise SecurityGatesError(f"{where}.{name}.tools must be a list of strings")
    timeout = body.get("timeout")
    if timeout is not None and not (isinstance(timeout, int) and timeout >= 0):
        raise SecurityGatesError(f"{where}.{name}.timeout must be a non-negative int")
    cost_cap = body.get("cost-cap")
    if cost_cap is not None and not (isinstance(cost_cap, (int, float)) and cost_cap >= 0):
        raise SecurityGatesError(f"{where}.{name}.cost-cap must be a non-negative number")
    scanner_pair: tuple[str, ...] = ()
    if "scanner-pair" in body:
        if name != SCANNER_LAYER:
            raise SecurityGatesError(
                f"{where}.{name}: scanner-pair is only meaningful on {SCANNER_LAYER!r}"
            )
        scanner_pair = _parse_scanner_pair(body["scanner-pair"], f"{where}.{name}")
    if "opt-in" in body:
        # opt-in is a PER-REPO decision on ci-slow only (spec: "advisory, opt-in per
        # repo"). It is meaningless on a language default — a default that forced
        # opt-in would make every repo of that language opted-in, contradicting
        # "per repo" and silently un-clearable (the bug plugin's sghc 1.4 found).
        if name != OPT_IN_LAYER:
            raise SecurityGatesError(
                f"{where}.{name}: opt-in is only meaningful on {OPT_IN_LAYER!r}"
            )
        if not allow_opt_in:
            raise SecurityGatesError(
                f"{where}.{name}: opt-in is a per-repo decision — set it under "
                f"repos.<repo>.{OPT_IN_LAYER}, not a language default"
            )
    return LayerGate(
        layer=name,
        tools=tuple(tools),
        blocking=bool(body.get("blocking", _DEFAULT_BLOCKING[name])),
        timeout=timeout,
        scanner_pair=scanner_pair,
        opt_in=bool(body.get("opt-in", False)),
        cost_cap=float(cost_cap) if cost_cap is not None else None,
    )


def _parse_layer_set(raw: Any, where: str, *, allow_opt_in: bool) -> dict[str, LayerGate]:
    body = _as_mapping(raw, where)
    return {
        name: _parse_layer(name, sub, where, allow_opt_in=allow_opt_in)
        for name, sub in body.items()
    }


def parse_security_gates(block: Any) -> SecurityGatesConfig:
    """Parse and validate a ``security-gates:`` block into a :class:`SecurityGatesConfig`.

    ``None`` / absent yields an empty config (Hook C simply has nothing configured).
    Validates layer names, per-layer field names and types, and the 2-of-3 scanner
    pair. Raises :class:`SecurityGatesError` on the first malformation — the block
    is policy config, so a typo must surface loudly, not resolve to a silent default.
    """
    if block is None:
        return SecurityGatesConfig()
    body = _as_mapping(block, "security-gates")
    languages: dict[str, dict[str, LayerGate]] = {}
    for lang, raw in _as_mapping(body.get("languages", {}), "security-gates.languages").items():
        languages[lang] = _parse_layer_set(
            raw, f"security-gates.languages.{lang}", allow_opt_in=False
        )

    repos: dict[str, dict[str, Any]] = {}
    for repo, raw in _as_mapping(body.get("repos", {}), "security-gates.repos").items():
        rbody = _as_mapping(raw, f"security-gates.repos.{repo}")
        entry: dict[str, Any] = {}
        if "opt-out" in rbody:
            entry["opt-out"] = bool(rbody["opt-out"])
        if "languages" in rbody:
            langs = rbody["languages"]
            if not (isinstance(langs, list) and all(isinstance(x, str) for x in langs)):
                raise SecurityGatesError(
                    f"security-gates.repos.{repo}.languages must be a list of strings"
                )
            entry["languages"] = tuple(langs)
        overrides = {k: v for k, v in rbody.items() if k in LAYERS}
        if overrides:
            entry["layers"] = _parse_layer_set(
                overrides, f"security-gates.repos.{repo}", allow_opt_in=True
            )
        stray = set(rbody) - {"opt-out", "languages"} - set(LAYERS)
        if stray:
            raise SecurityGatesError(
                f"security-gates.repos.{repo}: unknown key(s) {sorted(stray)!r}"
            )
        repos[repo] = entry

    note = body.get("relocation-note", RELOCATION_NOTE)
    if not isinstance(note, str):
        raise SecurityGatesError("security-gates.relocation-note must be a string")
    return SecurityGatesConfig(languages=languages, repos=repos, relocation_note=note)


def resolve_security_gates(
    platform_config: Mapping[str, Any],
    verification_gates_config: Mapping[str, Any] | None = None,
) -> SecurityGatesConfig:
    """Parse the ``security-gates`` block from WHEREVER it currently lives.

    The block is migrating from ``platform.yaml`` into ``verification-gates.yaml`` (csp
    1.2 — the single home for gate config, retiring :data:`RELOCATION_NOTE`). This
    resolver reads the NEW home first and falls back to ``platform.yaml``, so a
    consumer's call site need not know which file won — the migration is a core-side
    change, not a two-location fallback every reader re-derives (cli's ask
    20261001T123147). Returns an empty config when neither declares the block.

    Pass the parsed ``verification-gates.yaml`` as *verification_gates_config* (its
    top-level mapping, which may carry a ``security-gates`` key once migrated) and the
    parsed ``platform.yaml`` as *platform_config*.
    """
    if verification_gates_config is not None and "security-gates" in verification_gates_config:
        return parse_security_gates(verification_gates_config["security-gates"])
    return parse_security_gates(platform_config.get("security-gates"))


def _merge_layer(name: str, parts: list[LayerGate], override: LayerGate | None) -> LayerGate:
    """Fold one layer across a repo's languages, then apply the repo override.

    Union of ``tools`` (order-stable, deduped); ``blocking`` is True if ANY language
    marks it blocking (fail-safe — a language that wants the block wins); ``timeout``
    is the MAX (the longest a language needs); ``cost_cap`` is the min set (the
    tightest cap). The repo override replaces any field it sets, including the
    scanner pair (a per-repo record).

    ``opt_in`` is the one field that is NOT derived from language defaults: it is a
    per-repo decision (opt-in per repo), so it comes solely from the repo override —
    True only when this repo opted into ci-slow, False otherwise (fail-safe default;
    a repo with no override is not opted in). This answers "did THIS repo opt in?",
    not "is this an opt-in layer?" (the conflation plugin's sghc 1.4 surfaced).
    """
    tools: list[str] = []
    for p in parts:
        tools.extend(t for t in p.tools if t not in tools)
    blocking = any(p.blocking for p in parts) if parts else _DEFAULT_BLOCKING[name]
    timeouts = [p.timeout for p in parts if p.timeout is not None]
    timeout = max(timeouts) if timeouts else None
    caps = [p.cost_cap for p in parts if p.cost_cap is not None]
    cost_cap = min(caps) if caps else None
    scanner_pair = next((p.scanner_pair for p in parts if p.scanner_pair), ())
    opt_in = override.opt_in if override is not None else False

    if override is not None:
        if override.tools:
            tools = [t for t in override.tools]
        blocking = override.blocking
        if override.timeout is not None:
            timeout = override.timeout
        if override.cost_cap is not None:
            cost_cap = override.cost_cap
        if override.scanner_pair:
            scanner_pair = override.scanner_pair
    return LayerGate(
        layer=name,
        tools=tuple(tools),
        blocking=blocking,
        timeout=timeout,
        scanner_pair=scanner_pair,
        opt_in=opt_in,
        cost_cap=cost_cap,
    )


def resolve_repo_gates(
    config: SecurityGatesConfig, repo: str, languages: tuple[str, ...] | None = None
) -> RepoSecurityGates:
    """Resolve the effective gates for *repo*.

    A repo listed with ``opt-out: true`` is skipped VISIBLY — the result carries
    ``opt_out=True`` and no layers, so a surface can state "skipped", never silently
    drop the hook (spec: a docs-only repo opt-out skips the hook visibly). Otherwise
    the repo's languages (its own ``languages:`` if set, else the *languages*
    argument the caller resolved from repo metadata) select the per-language
    defaults, which are UNIONED for a mixed-language repo (:func:`_merge_layer`) and
    then overlaid with any per-repo layer overrides. Layers are returned in the
    canonical :data:`LAYERS` order.
    """
    repo_cfg = config.repos.get(repo, {})
    if repo_cfg.get("opt-out"):
        return RepoSecurityGates(repo=repo, opt_out=True)
    langs = repo_cfg.get("languages") or languages or ()
    overrides: dict[str, LayerGate] = repo_cfg.get("layers", {})
    resolved: list[LayerGate] = []
    for name in LAYERS:
        parts = [
            config.languages[la][name]
            for la in langs
            if la in config.languages and name in config.languages[la]
        ]
        override = overrides.get(name)
        if not parts and override is None:
            continue  # this layer is not configured for this repo
        resolved.append(_merge_layer(name, parts, override))
    return RepoSecurityGates(
        repo=repo, opt_out=False, languages=tuple(langs), layers=tuple(resolved)
    )


__all__ = [
    "LAYERS",
    "OPT_IN_LAYER",
    "RELOCATION_NOTE",
    "SCANNERS",
    "SCANNER_LAYER",
    "SCANNER_PAIR_SIZE",
    "LayerGate",
    "RepoSecurityGates",
    "SecurityGatesConfig",
    "SecurityGatesError",
    "parse_security_gates",
    "resolve_repo_gates",
    "resolve_security_gates",
]
