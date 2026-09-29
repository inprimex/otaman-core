"""The delivery authorization envelope — Layer 3 of delivery-authorization-envelope.

An accepted change MAY declare ``authorizes:`` — a list of action classes, each
optionally target-scoped — reviewed and approved as part of spec-approval. An
in-envelope class whose runtime is honored proceeds without a mid-execution
prompt; anything else (an undeclared class, a ``runtime-honored: limited`` class,
a scoped class off its target) is NOT authorized and escalates via
``decision-required`` rather than freezing a pane. The envelope expires when the
change archives — no carryover (design D3).

This module is the single home (design D1) for three shared facts, consumed by the
cli propose/approve surface (2.2) and the plugin dispatch (2.3):

- :data:`CLASS_REGISTRY` — the canon action classes. Adding one is a spec change;
  an unlisted action has no class, sits outside every envelope, and halts loudly
  (design D2, fail-safe). Each class carries a ``runtime-honored`` marker that is a
  MEASURED fact (design D6), not an assumption: it starts :data:`LIMITED` here —
  the fail-safe unmeasured value, promising no autonomy the runtime might refuse —
  and cli 2.2 records the measured value per class.
- :data:`FLOOR` — actions that are NEVER pre-authorizable, envelope or not
  (design D4). Naming one in an envelope refuses at parse, naming the floor.
- :func:`parse_envelope` / :func:`validate_envelope` — the parser and the
  approve-time gate; :func:`proceeds_without_prompt` — the delivery-time question.

Safety posture: authorization is *allowlist* only. :func:`proceeds_without_prompt`
returns True for exactly the honored, in-scope, registry-listed classes and False
for everything else, so a class this module does not recognize — a floor action
under a name not yet enumerated included — can never be authorized. The floor
enumeration adds a CRISP propose-time refusal on top of that base; it is not what
keeps the floor from being authorized.
"""

from __future__ import annotations

from typing import Any

#: ``runtime-honored`` marker values (design D6). ``YES``: the agent runtime's own
#: permission layer no longer prompts for this class, so an in-envelope declaration
#: genuinely proceeds unprompted. ``LIMITED``: the runtime still prompts (or only
#: partially honors it) — declaration authorizes nothing and routes to
#: decision-required. The envelope never promises autonomy the runtime refuses.
YES = "yes"
LIMITED = "limited"
RUNTIME_HONORED_VALUES = (YES, LIMITED)

#: The canon action-class registry (design D2). Class name -> measured
#: ``runtime-honored`` marker. Additions are spec changes — keeping the vocabulary
#: reviewable and stopping per-change invented verbs. Every class starts ``LIMITED``:
#: the fail-safe unmeasured value. cli 2.2 measures each class against the live
#: runtime and records the result here (design D6 — measurement, not assumption).
CLASS_REGISTRY: dict[str, str] = {
    "hooks-wiring": LIMITED,
    "release-publish": LIMITED,
    "branch-protection": LIMITED,
    "tenant-ssh-write": LIMITED,
    "schema-migration": LIMITED,
    "service-restart": LIMITED,
    "org-secret-write": LIMITED,
}

#: The floor (design D4): never pre-authorizable, envelope or not. Identifier ->
#: human-readable description. Naming any of these in an ``authorizes:`` block —
#: however scoped — refuses at parse, naming the floor. Short by design: a long
#: floor becomes a parallel permission system nobody reads. Identifiers are pending
#: canon confirmation from spec-agent; the allowlist base (see module docstring)
#: means the floor is safe regardless of whether this enumeration is exhaustive.
FLOOR: dict[str, str] = {
    "history-rewrite": "history rewrites / force pushes to protected branches",
    "tenant-or-human-data-delete": "deletion of tenant or human data",
    "working-tree-destroy": "destruction of uncommitted working trees",
    "credential-write-outside-connections": (
        "credential or key material written outside the connections layer"
    ),
    "publish-outside-release": "outward-facing publication outside the release pipeline",
    "act-as-human": "any action answering, signing, or acking as the human",
}


class EnvelopeError(ValueError):
    """A malformed envelope, an unknown class, or a floor violation."""


#: A parsed envelope: action class -> its target scope (a list of targets), or
#: ``None`` for an unscoped class that covers every target.
Envelope = dict[str, "list[str] | None"]


def parse_envelope(raw: Any) -> Envelope:
    """Parse an ``authorizes:`` value into a class -> scope mapping.

    Accepts what a change author writes under ``authorizes:``:

    - ``None`` / missing -> ``{}`` (a change with no envelope authorizes nothing).
    - a list whose items are each either a bare class name (``hooks-wiring``,
      unscoped) or a single-key mapping (``{release-publish: [otaman-deploy]}``,
      scoped to a target list).

    Raises :class:`EnvelopeError` on any other shape — a mapping at top level, a
    multi-key item, a non-string class, a non-list / non-string-element scope. This
    is shape validation only; :func:`validate_envelope` applies the registry and
    floor rules. A class named more than once merges its scopes (an unscoped
    mention widens the class to every target).
    """
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise EnvelopeError(
            f"authorizes: must be a list of action classes, got {type(raw).__name__}"
        )
    out: Envelope = {}
    for item in raw:
        if isinstance(item, str):
            name, scope = item, None
        elif isinstance(item, dict):
            if len(item) != 1:
                raise EnvelopeError(
                    "each authorizes: entry is one class (a name, or a single-key "
                    f"'class: [targets]' mapping); got {sorted(item)!r}"
                )
            ((name, raw_scope),) = item.items()
            if not isinstance(name, str):
                raise EnvelopeError(f"action class must be a string, got {name!r}")
            if not (isinstance(raw_scope, list) and all(isinstance(t, str) for t in raw_scope)):
                raise EnvelopeError(
                    f"scope for {name!r} must be a list of target strings, got {raw_scope!r}"
                )
            scope = list(raw_scope)
        else:
            raise EnvelopeError(
                "each authorizes: entry is a class name or a 'class: [targets]' "
                f"mapping, got {type(item).__name__}"
            )
        if name in out:
            prev = out[name]
            # An unscoped mention (None) widens to every target and wins; otherwise
            # union the target lists, order-stable.
            if prev is None or scope is None:
                out[name] = None
            else:
                out[name] = prev + [t for t in scope if t not in prev]
        else:
            out[name] = scope
    return out


def validate_envelope(raw: Any) -> Envelope:
    """Parse and gate an ``authorizes:`` value for the propose/approve surface.

    Runs :func:`parse_envelope`, then enforces the two approve-time rules:

    - a floor action (:data:`FLOOR`) named however scoped refuses, naming the
      floor — it is never pre-authorizable (design D4);
    - any remaining class not in :data:`CLASS_REGISTRY` refuses as unknown —
      classes are canon, additions are spec changes (design D2).

    Returns the parsed :data:`Envelope` when clean; raises :class:`EnvelopeError`
    on the first violation. The floor is checked before the unknown-class rule so a
    floor action gets the floor message rather than a generic "unknown class".
    """
    envelope = parse_envelope(raw)
    for name in envelope:
        if name in FLOOR:
            raise EnvelopeError(
                f"{name!r} names a floor action ({FLOOR[name]}); the floor is never "
                "pre-authorizable, envelope or not"
            )
    for name in envelope:
        if name not in CLASS_REGISTRY:
            raise EnvelopeError(
                f"unknown action class {name!r}; classes are canon "
                f"(one of: {', '.join(sorted(CLASS_REGISTRY))}) — additions are spec changes"
            )
    return envelope


def is_floor(action_class: str) -> bool:
    """Whether *action_class* is a floor action (never pre-authorizable)."""
    return action_class in FLOOR


def runtime_honored(action_class: str) -> str | None:
    """The measured ``runtime-honored`` marker of *action_class*, or ``None``.

    ``None`` means the class is not in the registry (so it is not authorizable at
    all). See :data:`YES` / :data:`LIMITED`.
    """
    return CLASS_REGISTRY.get(action_class)


def proceeds_without_prompt(
    envelope: Envelope, action_class: str, target: str | None = None
) -> bool:
    """Whether *action_class* on *target* proceeds without a mid-execution prompt.

    True only for the fully-authorized case — the delivery-time question. Every
    False case escalates via ``decision-required`` rather than freezing:

    - the class is a floor action, or not in the registry -> never;
    - the class is not declared in this envelope -> outside every envelope;
    - the class is ``runtime-honored: limited`` -> declaration authorizes nothing;
    - the class is declared with a scope and *target* is not in it (or *target* is
      omitted for a scoped class) -> off-scope.

    An unscoped declared class (scope ``None``) covers every target.
    """
    if action_class in FLOOR or action_class not in CLASS_REGISTRY:
        return False
    if action_class not in envelope:
        return False
    if CLASS_REGISTRY[action_class] != YES:
        return False
    scope = envelope[action_class]
    if scope is None:
        return True
    return target is not None and target in scope


__all__ = [
    "CLASS_REGISTRY",
    "FLOOR",
    "LIMITED",
    "RUNTIME_HONORED_VALUES",
    "YES",
    "Envelope",
    "EnvelopeError",
    "is_floor",
    "parse_envelope",
    "proceeds_without_prompt",
    "runtime_honored",
    "validate_envelope",
]
