"""Policy engine — the one composition algebra for every governance pack (policy-engine 1.1-1.3).

Policies live in the program meta repo beside platform.yaml:

    policy/index.yaml              # registered packs + their narrow-only rules + schema version
    policy/<pack>/<name>.yaml      # a named policy for a pack (e.g. git/standard.yaml)

The **effective policy** for a pack is the tightest-wins intersection of three
layers — org ceiling → program profile → agent profile (D2). A lower layer MAY
tighten and MAY NOT loosen any rule the pack marks ``narrow-only`` (all
git-merge-authority rules are narrow-only); a refused loosening is surfaced by
``otaman doctor``. One algebra serves every pack (git, spec-lifecycle,
guardrails, and the permissions pack alike), so the CTO edits them in one place.

This module owns the ENGINE (model, loader, algebra, selection, the shipped git
standard content, branch-owner resolution, and the doctor drift/owner-less/
deprecation checks). Generating enforcement (branch protection, CODEOWNERS,
CLAUDE.local.md) and applying it live are the cli/plugin/deploy steps — they read
the effective policy through :func:`effective_policy`, the single read point.

Selection (D3): platform.yaml ``policies: {git: standard, ...}`` is the program
default; ``repos[].policies.<pack>`` and ``agents[].policies.<pack>`` override,
subject to the algebra. Absent selection means ``standard``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from otaman_core.human_roster import DoctorFinding

POLICY_SCHEMA_VERSION = 1

#: The three composition layers, broadest → nearest (D2).
LAYERS: tuple[str, ...] = ("org", "program", "agent")

DEFAULT_POLICY_NAME = "standard"


class PolicyError(ValueError):
    """Raised when a policy file / index is structurally invalid."""


@dataclass(frozen=True)
class PackSpec:
    """A registered pack from ``index.yaml``: its narrow-only rule keys.

    ``narrow_only`` rules are boolean restrictions (``true`` = restriction on =
    tighter) that a lower layer may only tighten, never loosen.
    """

    name: str
    narrow_only: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PolicyIndex:
    schema_version: int
    packs: dict[str, PackSpec]

    def narrow_only(self, pack: str) -> frozenset[str]:
        spec = self.packs.get(pack)
        return spec.narrow_only if spec is not None else frozenset()


@dataclass(frozen=True)
class Policy:
    """A named policy for a pack — a bag of rule ``key → value``."""

    pack: str
    name: str
    rules: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectivePolicy:
    """The composed, tightest-wins result for one pack."""

    pack: str
    rules: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LooseningViolation:
    """A lower layer's refused attempt to loosen a ``narrow-only`` rule."""

    pack: str
    rule: str
    layer: str
    attempted: Any
    kept: Any


# ---------------------------------------------------------------------------
# Loader + validation (1.1)


def policy_dir(meta_root: Path) -> Path:
    """The policy folder for a program: ``<meta_root>/policy``."""
    return meta_root / "policy"


def parse_policy_index(data: Any) -> PolicyIndex:
    """Parse ``index.yaml`` content. Absent/empty → an empty index (no packs)."""
    if data is None:
        return PolicyIndex(schema_version=POLICY_SCHEMA_VERSION, packs={})
    if not isinstance(data, dict):
        raise PolicyError("policy/index.yaml must be a mapping")
    version = data.get("schema_version", POLICY_SCHEMA_VERSION)
    if not isinstance(version, int):
        raise PolicyError("policy/index.yaml 'schema_version' must be an integer")
    packs_raw = data.get("packs", {})
    if not isinstance(packs_raw, dict):
        raise PolicyError("policy/index.yaml 'packs' must be a mapping")
    packs: dict[str, PackSpec] = {}
    for name, spec in packs_raw.items():
        spec = spec or {}
        if not isinstance(spec, dict):
            raise PolicyError(f"policy pack {name!r}: entry must be a mapping")
        no_raw = spec.get("narrow_only", spec.get("narrow-only", []))
        if not isinstance(no_raw, list) or not all(isinstance(k, str) for k in no_raw):
            raise PolicyError(f"policy pack {name!r}: 'narrow_only' must be a list of rule keys")
        packs[name] = PackSpec(name=name, narrow_only=frozenset(no_raw))
    return PolicyIndex(schema_version=version, packs=packs)


def load_policy_index(meta_root: Path) -> PolicyIndex:
    """Load ``policy/index.yaml``. Absent file → an empty index."""
    import yaml

    path = policy_dir(meta_root) / "index.yaml"
    if not path.is_file():
        return PolicyIndex(schema_version=POLICY_SCHEMA_VERSION, packs={})
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"policy/index.yaml is unreadable: {exc}") from exc
    return parse_policy_index(data)


def parse_policy(pack: str, name: str, data: Any) -> Policy:
    """Parse one ``policy/<pack>/<name>.yaml`` mapping into a :class:`Policy`."""
    if data is None:
        return Policy(pack=pack, name=name, rules={})
    if not isinstance(data, dict):
        raise PolicyError(f"policy {pack}/{name}: must be a mapping")
    # `pack`/`name` in the file (if present) must agree with the path.
    if data.get("pack", pack) != pack:
        raise PolicyError(f"policy {pack}/{name}: 'pack' field {data.get('pack')!r} != {pack!r}")
    if data.get("name", name) != name:
        raise PolicyError(f"policy {pack}/{name}: 'name' field {data.get('name')!r} != {name!r}")
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise PolicyError(f"policy {pack}/{name}: 'rules' must be a mapping")
    return Policy(pack=pack, name=name, rules=dict(rules))


def load_policy(meta_root: Path, pack: str, name: str) -> Policy | None:
    """Load ``policy/<pack>/<name>.yaml``, or ``None`` when the file is absent."""
    import yaml

    path = policy_dir(meta_root) / pack / f"{name}.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"policy {pack}/{name} is unreadable: {exc}") from exc
    return parse_policy(pack, name, data)


# ---------------------------------------------------------------------------
# Composition algebra (1.1)


def compose(
    layers: Sequence[tuple[str, Policy]],
    narrow_only: frozenset[str] | set[str],
) -> tuple[EffectivePolicy, list[LooseningViolation]]:
    """Compose ordered layers (broadest → nearest) into the effective policy.

    Rule resolution:
    - **narrow-only** rules are boolean restrictions (``True`` = tighter). The
      effective value is ``True`` if any layer sets it — a lower layer that sets
      ``False`` after a higher layer set ``True`` is a REFUSED loosening: the
      ``True`` is kept and a :class:`LooseningViolation` is recorded (doctor names
      it). A non-bool value for a narrow-only rule is a :class:`PolicyError`.
    - **other** rules are nearest-scope-wins (a later layer overrides), matching
      the config-cascade model — they are profile/config, not restrictions.

    Returns the effective policy plus any refused loosenings.
    """
    if not layers:
        return EffectivePolicy(pack="", rules={}), []
    pack = layers[0][1].pack
    narrow = set(narrow_only)
    effective: dict[str, Any] = {}
    violations: list[LooseningViolation] = []

    for layer_name, policy in layers:
        for rule, value in policy.rules.items():
            if rule in narrow:
                if not isinstance(value, bool):
                    raise PolicyError(
                        f"policy {pack}: narrow-only rule {rule!r} must be boolean, "
                        f"got {type(value).__name__}"
                    )
                current = effective.get(rule)
                if current is True and value is False:
                    # attempted loosening — refuse, keep the tighter True
                    violations.append(
                        LooseningViolation(
                            pack=pack,
                            rule=rule,
                            layer=layer_name,
                            attempted=False,
                            kept=True,
                        )
                    )
                else:
                    # tighten (False→True) or first-set; True wins forever
                    effective[rule] = bool(current) or value
            else:
                effective[rule] = value  # nearest-wins

    return EffectivePolicy(pack=pack, rules=effective), violations


# ---------------------------------------------------------------------------
# Selection resolution → effective-policy API (1.2)


def _selection_for_layer(block: Any, pack: str) -> str | None:
    """Read a ``policies: {<pack>: <name>}`` selection from a config block."""
    if not isinstance(block, dict):
        return None
    policies = block.get("policies")
    if not isinstance(policies, dict):
        return None
    name = policies.get(pack)
    return name if isinstance(name, str) and name else None


def select_policy_names(
    platform_config: Mapping[str, Any],
    pack: str,
    *,
    repo: str | None = None,
    agent: str | None = None,
) -> list[tuple[str, str]]:
    """Resolve the ordered (layer, policy-name) selection for a pack.

    Program default from ``policies:``; a ``repos[<repo>].policies`` and/or
    ``agents[<agent>].policies`` entry adds a nearer layer. Absent selection at a
    layer contributes ``standard``. Returns broadest → nearest for :func:`compose`.
    """
    program = _selection_for_layer(platform_config, pack) or DEFAULT_POLICY_NAME
    out: list[tuple[str, str]] = [("program", program)]

    if repo is not None:
        for entry in platform_config.get("repos", []) or []:
            if isinstance(entry, dict) and entry.get("name") == repo:
                name = _selection_for_layer(entry, pack)
                if name:
                    out.append(("repo", name))
                break
    if agent is not None:
        agents = platform_config.get("agents", [])
        entries = agents if isinstance(agents, list) else []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name") == agent:
                name = _selection_for_layer(entry, pack)
                if name:
                    out.append(("agent", name))
                break
    return out


def effective_policy(
    meta_root: Path,
    platform_config: Mapping[str, Any],
    pack: str,
    *,
    repo: str | None = None,
    agent: str | None = None,
) -> tuple[EffectivePolicy, list[LooseningViolation]]:
    """THE single read point: resolve + load + compose the effective policy.

    cli (verbs, merge guard) and plugin (generation) call this. A selected policy
    that is missing on disk falls back to the shipped standard for the pack (so a
    fresh program with only ``policies: {git: standard}`` still resolves).
    """
    index = load_policy_index(meta_root)
    narrow = index.narrow_only(pack)
    layers: list[tuple[str, Policy]] = []
    for layer_name, name in select_policy_names(platform_config, pack, repo=repo, agent=agent):
        policy = load_policy(meta_root, pack, name)
        if policy is None and name == DEFAULT_POLICY_NAME:
            policy = shipped_standard(pack)
        if policy is None:
            raise PolicyError(
                f"policy {pack}/{name} selected ({layer_name}) but not found under "
                f"{policy_dir(meta_root)}"
            )
        layers.append((layer_name, policy))
    return compose(layers, narrow)


# ---------------------------------------------------------------------------
# Shipped git standard (1.2) — the content `otaman init --update` writes.
# platform.yaml keeps only the selection; the former standards.git.* content is
# absorbed here as pack rules (D6). Force-push forbidden (Roman 2026-08-31).

GIT_PACK_NARROW_ONLY: frozenset[str] = frozenset(
    {
        "force_push_forbidden",
        "owner_admission_required",
        "agents_merge_human_owned_branch_forbidden",
    }
)

GIT_STANDARD_RULES: dict[str, Any] = {
    # narrow-only merge-authority restrictions (a lower layer may only tighten):
    "force_push_forbidden": True,
    "owner_admission_required": True,
    "agents_merge_human_owned_branch_forbidden": True,
    # non-narrow-only rules (profile/config; nearest-wins):
    "agent_self_merge_on_owned_repo": True,
    "branch_owner_convention": "<type>/<owner>/<topic>",
    # absorbed standards.git.* content (JTBD-45) — carried verbatim by callers:
    "branching": None,  # populated from platform.yaml standards.git.branching on init --update
    "environments": None,
    "merge_policy": None,
}


def shipped_standard(pack: str) -> Policy:
    """The Otaman-shipped ``standard`` policy for ``pack``.

    ``otaman init --update`` writes this to ``policy/<pack>/standard.yaml`` only
    when the file is absent (never overwriting a CTO edit). Currently only the
    ``git`` pack ships a standard.
    """
    if pack == "git":
        return Policy(pack="git", name=DEFAULT_POLICY_NAME, rules=dict(GIT_STANDARD_RULES))
    raise PolicyError(f"no shipped standard for pack {pack!r}")


def shipped_index() -> PolicyIndex:
    """The index Otaman ships (registers the git pack + its narrow-only rules)."""
    return PolicyIndex(
        schema_version=POLICY_SCHEMA_VERSION,
        packs={"git": PackSpec(name="git", narrow_only=GIT_PACK_NARROW_ONLY)},
    )


# ---------------------------------------------------------------------------
# Git pack: branch-owner resolution (D6) — feeds doctor + cli/plugin


_BRANCH_CONVENTION = re.compile(r"^[^/]+/(?P<owner>[^/]+)/.+$")


def resolve_branch_owner(
    branch: str,
    *,
    repo_owner: str | None = None,
    is_default_branch: bool = False,
    branch_owners: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a branch's owner, or ``None`` when owner-less (D6).

    Precedence: explicit ``branch_owners`` registry entry →
    ``<type>/<owner>/<topic>`` convention → the repo's declared owner for the
    default branch. ``None`` means owner-less (doctor flags it; PRs targeting it
    are unadmittable).
    """
    if branch_owners and branch in branch_owners:
        return branch_owners[branch]
    m = _BRANCH_CONVENTION.match(branch)
    if m:
        return m.group("owner")
    if is_default_branch and repo_owner:
        return repo_owner
    return None


# ---------------------------------------------------------------------------
# Doctor checks (1.3)

#: Doctor enforcement modes. otaman-dev runs ``block``; tenants default to ``warn``.
DOCTOR_MODES: tuple[str, ...] = ("block", "warn")


def _finding(mode: str, message: str) -> DoctorFinding:
    """ERROR in block mode, WARN in warn mode."""
    return DoctorFinding("error" if mode == "block" else "warn", message)


def check_policy_drift(
    effective: EffectivePolicy,
    live_rules: Mapping[str, Any],
    *,
    mode: str = "warn",
    repo: str = "?",
) -> list[DoctorFinding]:
    """Compare live enforcement (injected) to the effective policy; report drift.

    ``live_rules`` is the caller-supplied observed state (e.g. the repo's actual
    branch-protection settings, fetched by the cli/git-host layer — core does no
    network I/O). Any policy rule whose live value differs is drift: ERROR in
    ``block`` mode, WARN in ``warn`` mode, naming the repo, rule, and remedy.
    """
    findings: list[DoctorFinding] = []
    for rule, want in sorted(effective.rules.items()):
        if want is None:
            continue  # unset/absorbed placeholder — nothing to enforce
        have = live_rules.get(rule)
        if have != want:
            findings.append(
                _finding(
                    mode,
                    f"policy drift on repo {repo!r}: rule {rule!r} is {have!r} but "
                    f"'{effective.pack}' policy requires {want!r} — run `otaman policy apply`",
                )
            )
    return findings


def check_owner_less_branches(
    branches: Sequence[str],
    *,
    repo_owner: str | None = None,
    default_branch: str | None = None,
    branch_owners: Mapping[str, str] | None = None,
) -> list[DoctorFinding]:
    """WARN each branch with no resolvable owner (D6) — PRs to it are unadmittable."""
    findings: list[DoctorFinding] = []
    for branch in branches:
        owner = resolve_branch_owner(
            branch,
            repo_owner=repo_owner,
            is_default_branch=(branch == default_branch),
            branch_owners=branch_owners,
        )
        if owner is None:
            findings.append(
                DoctorFinding(
                    "warn",
                    f"branch {branch!r} has no resolvable owner (no <type>/<owner>/<topic> "
                    "match and no branch-owners entry); PRs targeting it are unadmittable",
                )
            )
    return findings


#: platform.yaml keys absorbed into the git pack — honored one cycle, then removed (D8).
DEPRECATED_STANDARDS_KEYS: tuple[str, ...] = ("branching", "environments", "merge_policy")


def check_deprecated_standards(platform_config: Mapping[str, Any]) -> list[DoctorFinding]:
    """WARN when legacy ``standards.git.*`` keys are still present (D8, one cycle).

    Their content is now pack rules in ``policy/git/standard.yaml``; the old keys
    are honored for one deprecation cycle, then removed.
    """
    standards = platform_config.get("standards")
    git = standards.get("git") if isinstance(standards, dict) else None
    if not isinstance(git, dict):
        return []
    present = [k for k in DEPRECATED_STANDARDS_KEYS if k in git]
    if not present:
        return []
    return [
        DoctorFinding(
            "warn",
            f"platform.yaml standards.git.{{{', '.join(present)}}} is deprecated — its content "
            "moved to policy/git/standard.yaml (policy-engine D8); remove the old keys next cycle",
        )
    ]


__all__ = [
    "DEFAULT_POLICY_NAME",
    "DEPRECATED_STANDARDS_KEYS",
    "DOCTOR_MODES",
    "GIT_PACK_NARROW_ONLY",
    "GIT_STANDARD_RULES",
    "LAYERS",
    "POLICY_SCHEMA_VERSION",
    "DoctorFinding",
    "EffectivePolicy",
    "LooseningViolation",
    "PackSpec",
    "Policy",
    "PolicyError",
    "PolicyIndex",
    "check_deprecated_standards",
    "check_owner_less_branches",
    "check_policy_drift",
    "compose",
    "effective_policy",
    "load_policy",
    "load_policy_index",
    "parse_policy",
    "parse_policy_index",
    "policy_dir",
    "resolve_branch_owner",
    "select_policy_names",
    "shipped_index",
    "shipped_standard",
]
