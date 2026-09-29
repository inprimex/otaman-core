"""delivery-authorization-envelope 2.1 — the Layer 3 authorization envelope.

Pins the canon class registry (with fail-safe ``runtime-honored`` markers), the
``authorizes:`` parser (bare + scoped forms, dedupe/widen), the approve-time gate
(floor refusal named as such, unknown-class refusal), and the delivery-time
``proceeds_without_prompt`` allowlist (honored + in-scope only; everything else
escalates).
"""

from __future__ import annotations

import pytest

from otaman_core.delivery_envelope import (
    CLASS_REGISTRY,
    FLOOR,
    LIMITED,
    YES,
    EnvelopeError,
    is_floor,
    parse_envelope,
    proceeds_without_prompt,
    runtime_honored,
    validate_envelope,
)

# ---------------------------------------------------------------------------
# registry canon


def test_registry_is_the_seven_canon_classes():
    assert set(CLASS_REGISTRY) == {
        "hooks-wiring",
        "release-publish",
        "branch-protection",
        "tenant-ssh-write",
        "schema-migration",
        "service-restart",
        "org-secret-write",
    }


def test_all_classes_start_limited_failsafe():
    # unmeasured => LIMITED => authorizes nothing until cli 2.2 measures (design D6)
    assert all(v == LIMITED for v in CLASS_REGISTRY.values())


def test_runtime_honored_lookup():
    assert runtime_honored("release-publish") == LIMITED
    assert runtime_honored("not-a-class") is None


# ---------------------------------------------------------------------------
# parse_envelope — shapes


def test_parse_none_and_empty():
    assert parse_envelope(None) == {}
    assert parse_envelope([]) == {}


def test_parse_bare_class_is_unscoped():
    assert parse_envelope(["hooks-wiring"]) == {"hooks-wiring": None}


def test_parse_scoped_class():
    assert parse_envelope([{"release-publish": ["otaman-deploy"]}]) == {
        "release-publish": ["otaman-deploy"]
    }


def test_parse_mixed_bare_and_scoped():
    env = parse_envelope(["hooks-wiring", {"release-publish": ["otaman-deploy"]}])
    assert env == {"hooks-wiring": None, "release-publish": ["otaman-deploy"]}


def test_parse_duplicate_scoped_unions_targets():
    env = parse_envelope(
        [{"release-publish": ["otaman-deploy"]}, {"release-publish": ["otaman-web"]}]
    )
    assert env == {"release-publish": ["otaman-deploy", "otaman-web"]}


def test_parse_unscoped_mention_widens_over_scoped():
    env = parse_envelope([{"release-publish": ["otaman-deploy"]}, "release-publish"])
    assert env == {"release-publish": None}


@pytest.mark.parametrize(
    "raw",
    [
        {"hooks-wiring": None},  # top-level mapping, not a list
        [123],  # non-string, non-mapping item
        [{"a": [], "b": []}],  # multi-key item
        [{"release-publish": "otaman-deploy"}],  # scope not a list
        [{"release-publish": [1, 2]}],  # scope elements not strings
        [{123: ["x"]}],  # non-string class name
    ],
)
def test_parse_rejects_malformed(raw):
    with pytest.raises(EnvelopeError):
        parse_envelope(raw)


# ---------------------------------------------------------------------------
# validate_envelope — the approve-time gate


def test_validate_accepts_registry_classes():
    env = validate_envelope(["hooks-wiring", {"release-publish": ["otaman-deploy"]}])
    assert env == {"hooks-wiring": None, "release-publish": ["otaman-deploy"]}


def test_validate_refuses_unknown_class():
    with pytest.raises(EnvelopeError, match="unknown action class"):
        validate_envelope(["not-a-real-class"])


@pytest.mark.parametrize("floor_id", sorted(FLOOR))
def test_validate_refuses_every_floor_action(floor_id):
    with pytest.raises(EnvelopeError, match="floor"):
        validate_envelope([floor_id])


def test_validate_refuses_floor_however_scoped():
    # "however scoped" — a scoped floor action still refuses
    with pytest.raises(EnvelopeError, match="floor"):
        validate_envelope([{"history-rewrite": ["some-branch"]}])


def test_validate_floor_message_beats_unknown_for_floor_actions():
    # a floor action is named as a floor violation, not as a generic unknown class
    with pytest.raises(EnvelopeError, match="floor"):
        validate_envelope(["act-as-human"])


def test_is_floor():
    assert is_floor("history-rewrite") is True
    assert is_floor("hooks-wiring") is False


# ---------------------------------------------------------------------------
# proceeds_without_prompt — the delivery-time allowlist


def _honored(monkeypatch, action_class):
    """Pretend cli 2.2 measured *action_class* as runtime-honored: yes."""
    monkeypatch.setitem(CLASS_REGISTRY, action_class, YES)


def test_undeclared_class_does_not_proceed(monkeypatch):
    _honored(monkeypatch, "hooks-wiring")
    env = validate_envelope(["hooks-wiring"])
    assert proceeds_without_prompt(env, "schema-migration") is False  # not declared


def test_limited_class_does_not_proceed_even_when_declared():
    env = validate_envelope(["hooks-wiring"])  # hooks-wiring is LIMITED by default
    assert proceeds_without_prompt(env, "hooks-wiring") is False


def test_honored_declared_unscoped_proceeds(monkeypatch):
    _honored(monkeypatch, "hooks-wiring")
    env = validate_envelope(["hooks-wiring"])
    assert proceeds_without_prompt(env, "hooks-wiring") is True
    assert proceeds_without_prompt(env, "hooks-wiring", target="anything") is True


def test_honored_scoped_proceeds_only_in_scope(monkeypatch):
    _honored(monkeypatch, "release-publish")
    env = validate_envelope([{"release-publish": ["otaman-deploy"]}])
    assert proceeds_without_prompt(env, "release-publish", target="otaman-deploy") is True
    assert proceeds_without_prompt(env, "release-publish", target="otaman-web") is False
    # a scoped class with no target given is off-scope -> escalates
    assert proceeds_without_prompt(env, "release-publish") is False


def test_floor_never_proceeds(monkeypatch):
    # even if some future bug measured a floor id as honored and it slipped into an
    # envelope dict, the delivery gate still refuses it
    monkeypatch.setitem(CLASS_REGISTRY, "history-rewrite", YES)
    assert proceeds_without_prompt({"history-rewrite": None}, "history-rewrite") is False
