"""llm-router-backend 1.1 — the ModelBackend seam, routing, and the guard predicate.

Pins the two implementations (default byte-identical / litellm-proxy), opt-in config
(absent = default), single-point route resolution, and the sensitivity-guard predicate.
"""

from __future__ import annotations

import pytest

from otaman_core.llm_router import (
    DEFAULT_BACKEND,
    LITELLM_PROXY,
    BackendTarget,
    DefaultBackend,
    LiteLLMProxyBackend,
    ModelBackend,
    Route,
    RouterError,
    effective_route,
    guard_route,
    parse_router_config,
    route_leaves_tenant,
    select_backend,
)

# --- backends implement the seam ---------------------------------------------


def test_both_backends_satisfy_the_protocol():
    assert isinstance(DefaultBackend(), ModelBackend)
    assert isinstance(LiteLLMProxyBackend(base_url="http://x"), ModelBackend)


def test_default_backend_no_route_is_native():
    # the byte-identical path: no route -> native target, no proxy
    t = DefaultBackend().resolve(None)
    assert t == BackendTarget(family="anthropic", model=None, base_url=None, local=False)


def test_default_backend_with_route_stays_native():
    t = DefaultBackend().resolve(Route(family="openai", model="gpt-4o"))
    assert t.base_url is None  # DefaultBackend never proxies


def test_litellm_backend_routes_through_proxy():
    be = LiteLLMProxyBackend(base_url="http://localhost:4000")
    t = be.resolve(Route(family="ollama", model="llama3", local=True))
    assert t == BackendTarget(
        family="ollama", model="llama3", base_url="http://localhost:4000", local=True
    )


# --- config: opt-in ----------------------------------------------------------


def test_no_router_config_selects_default():
    assert select_backend({}).name == DEFAULT_BACKEND
    assert parse_router_config({}).backend is None


def test_litellm_config_selects_proxy():
    cfg = {"router": {"backend": "litellm-proxy", "base_url": "http://p:4000"}}
    be = select_backend(cfg)
    assert be.name == LITELLM_PROXY
    assert isinstance(be, LiteLLMProxyBackend) and be.base_url == "http://p:4000"


def test_local_only_classes_parsed():
    rc = parse_router_config(
        {
            "router": {
                "backend": "litellm-proxy",
                "base_url": "http://p",
                "local_only_classes": ["cofounder-only"],
            }
        }
    )
    assert rc.local_only_classes == frozenset({"cofounder-only"})


@pytest.mark.parametrize(
    "config",
    [
        {"router": "nope"},  # not a mapping
        {"router": {"backend": "openrouter"}},  # unknown backend
        {"router": {"backend": "litellm-proxy"}},  # missing base_url
        {"router": {"local_only_classes": "x"}},  # not a list
    ],
)
def test_parse_rejects_malformed(config):
    with pytest.raises(RouterError):
        parse_router_config(config)


# --- route resolution (core owns it) -----------------------------------------


def test_effective_route_array_form():
    cfg = {
        "agents": [
            {"name": "critic", "route": {"family": "openai", "model": "gpt-4o"}},
            {"name": "plain"},
        ]
    }
    assert effective_route(cfg, "critic") == Route(family="openai", model="gpt-4o")
    assert effective_route(cfg, "plain") is None
    assert effective_route(cfg, "absent") is None


def test_effective_route_string_shorthand_and_mapping_form():
    assert effective_route({"agents": [{"name": "a", "route": "anthropic"}]}, "a") == Route(
        family="anthropic"
    )
    # mapping agent-declaration form
    cfg = {"agents": {"a": {"route": {"family": "ollama", "local": True}}}}
    assert effective_route(cfg, "a") == Route(family="ollama", local=True)


def test_effective_route_rejects_bad_shape():
    with pytest.raises(RouterError):
        effective_route({"agents": [{"name": "a", "route": {"model": "x"}}]}, "a")  # no family


# --- Route.id: the canonical telemetry key (plugin lrb-1.6 question) ----------


def test_route_id_renders_every_shape():
    assert Route(family="anthropic", model="claude-opus-4").id == "anthropic/claude-opus-4"
    assert Route(family="openai").id == "openai"  # family-only
    assert Route(family="ollama", model="llama3", local=True).id == "ollama/llama3@local"
    assert Route(family="ollama", local=True).id == "ollama@local"  # family-only, local


def test_route_id_keeps_local_distinct_from_offtenant():
    # the correctness point: the SAME family/model run local vs off-tenant are different
    # routes for cost/sensitivity, so they must not collapse to one telemetry key
    # (a flat family/model rendering — like the call-site helper — would lose this).
    off = Route(family="ollama", model="llama3", local=False)
    local = Route(family="ollama", model="llama3", local=True)
    assert off.id != local.id
    assert off.id == "ollama/llama3" and local.id == "ollama/llama3@local"


def test_route_id_is_the_effective_route_key():
    # the whole point: effective_route(...).id is what a caller passes to record_critic_cost
    cfg = {"agents": [{"name": "a", "route": {"family": "ollama", "local": True}}]}
    assert effective_route(cfg, "a").id == "ollama@local"


# --- sensitivity guard predicate ---------------------------------------------


def test_route_leaves_tenant():
    assert route_leaves_tenant(BackendTarget("openai", None, "http://p", local=False)) is True
    assert route_leaves_tenant(BackendTarget("ollama", None, "http://p", local=True)) is False


def test_guard_refuses_cloud_route_for_local_only_content():
    rc = parse_router_config(
        {
            "router": {
                "backend": "litellm-proxy",
                "base_url": "http://p",
                "local_only_classes": ["cofounder-only"],
            }
        }
    )
    cloud = BackendTarget("openai", "gpt-4o", "http://p", local=False)
    refusal = guard_route(rc, cloud, {"cofounder-only"})
    assert refusal is not None
    assert "cofounder-only" in refusal and "leaves the tenant" in refusal


def test_guard_admits_local_route_for_same_content():
    rc = parse_router_config(
        {
            "router": {
                "backend": "litellm-proxy",
                "base_url": "http://p",
                "local_only_classes": ["cofounder-only"],
            }
        }
    )
    local = BackendTarget("ollama", "llama3", "http://p", local=True)
    assert guard_route(rc, local, {"cofounder-only"}) is None


def test_guard_admits_cloud_route_for_unguarded_content():
    rc = parse_router_config(
        {
            "router": {
                "backend": "litellm-proxy",
                "base_url": "http://p",
                "local_only_classes": ["cofounder-only"],
            }
        }
    )
    cloud = BackendTarget("openai", "gpt-4o", "http://p", local=False)
    assert guard_route(rc, cloud, {"public"}) is None
