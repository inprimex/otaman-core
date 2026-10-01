"""The HarnessBackend seam + LLM routing (llm-router-backend 1.1).

Today every LLM call rides one backend; there is no seam to express "this critic
runs on a different family" or "this content must stay local." This module is that
seam, defined MINIMALLY and single-home from birth (spec-agent ruling 20261001T120243
— it hosts routing, not a speculative full backend separation; a future JTBD-23 change
extends THIS, not the reverse). Consumed by the adapters litellm adapter (1.2), the
bridge routing dispatch + sensitivity guard (1.3), and the cli route surfaces + doctor
(1.4).

Two implementations, exactly:

- :class:`DefaultBackend` — wraps today's native call path. With no router config it is
  what :func:`select_backend` returns, and ``resolve(None)`` yields a native target
  (``base_url is None``): the call path is byte-identical to pre-change behavior (the
  opt-in-means-zero-change scenario).
- :class:`LiteLLMProxyBackend` — an opt-in OpenAI-compatible proxy (100+ targets incl.
  local Ollama/vLLM), configured per platform.yaml ``router:``.

Core owns ROUTE RESOLUTION (cli's question 20261001T135210): :func:`effective_route`
is the single place that reads an agent declaration's ``route``, so the cli doctor and
the bridge dispatch resolve identically rather than each parsing the raw field.

The sensitivity guard is enforced in the bridge dispatch path (1.3), never by prompt
convention; core supplies its PREDICATE — :func:`route_leaves_tenant` and
:func:`guard_route` — so the refusal (naming the class and target) is computed one way.
Core does not make LLM calls; it models the seam, resolves routes, and supplies the
guard predicate. Dispatch and the wire call live in bridge/adapters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: The opt-in backend name (platform.yaml ``router.backend``). Absent = DefaultBackend.
LITELLM_PROXY = "litellm-proxy"
DEFAULT_BACKEND = "default"


class RouterError(ValueError):
    """A malformed ``router:`` block or agent ``route`` declaration."""


@dataclass(frozen=True)
class Route:
    """What an agent declaration names: a model family / target.

    ``local`` marks a target that stays inside the tenant (e.g. Ollama/vLLM) — the
    one bit the sensitivity guard reads. ``model`` is the specific model id, optional
    (a family-only route lets the backend pick its default).
    """

    family: str
    model: str | None = None
    local: bool = False


@dataclass(frozen=True)
class BackendTarget:
    """Where a dispatched call goes — what bridge/adapters need to make the call.

    ``base_url is None`` means the native path (DefaultBackend): no proxy, today's
    behavior. A non-None ``base_url`` is the litellm proxy's OpenAI-compatible endpoint.
    ``local`` carries the route's tenant-residency bit through to the guard.
    """

    family: str
    model: str | None
    base_url: str | None
    local: bool


@runtime_checkable
class HarnessBackend(Protocol):
    """The seam: given an agent's :class:`Route` (or ``None``), where does the call go?"""

    @property
    def name(self) -> str:
        """The backend identifier (``default`` / ``litellm-proxy``)."""
        ...

    def resolve(self, route: Route | None) -> BackendTarget:
        """The :class:`BackendTarget` for *route* (``None`` = the backend's default)."""
        ...


@dataclass(frozen=True)
class DefaultBackend:
    """Today's native path. ``resolve(None)`` is byte-identical to pre-change behavior."""

    name: str = DEFAULT_BACKEND

    def resolve(self, route: Route | None) -> BackendTarget:
        # Native path: no proxy. A route may still name a model on the native family,
        # but base_url stays None so the call path is unchanged when no route is set.
        if route is None:
            return BackendTarget(family="anthropic", model=None, base_url=None, local=False)
        return BackendTarget(
            family=route.family or "anthropic",
            model=route.model,
            base_url=None,
            local=route.local,
        )


@dataclass(frozen=True)
class LiteLLMProxyBackend:
    """Opt-in OpenAI-compatible proxy; routes to 100+ targets incl. local Ollama/vLLM."""

    base_url: str
    name: str = LITELLM_PROXY

    def resolve(self, route: Route | None) -> BackendTarget:
        if route is None:
            return BackendTarget(
                family="anthropic", model=None, base_url=self.base_url, local=False
            )
        return BackendTarget(
            family=route.family, model=route.model, base_url=self.base_url, local=route.local
        )


@dataclass(frozen=True)
class RouterConfig:
    """Parsed ``router:`` block. ``backend is None`` = unconfigured (default path).

    ``local_only_classes`` are the sensitivity classes that must never leave the
    tenant — the guard refuses a non-local route for content carrying one of them.
    """

    backend: str | None = None
    base_url: str | None = None
    local_only_classes: frozenset[str] = frozenset()


def _route_from_raw(raw: Any, where: str) -> Route:
    """Parse an agent declaration's ``route`` (an object; a bare string = family only)."""
    if isinstance(raw, str):
        if not raw:
            raise RouterError(f"{where} route string must be non-empty")
        return Route(family=raw)
    if not isinstance(raw, Mapping):
        raise RouterError(f"{where} route must be a string or a mapping")
    family = raw.get("family")
    if not isinstance(family, str) or not family:
        raise RouterError(f"{where}.route.family is required (non-empty string)")
    model = raw.get("model")
    if model is not None and not isinstance(model, str):
        raise RouterError(f"{where}.route.model must be a string")
    local = raw.get("local", False)
    if not isinstance(local, bool):
        raise RouterError(f"{where}.route.local must be a boolean")
    return Route(family=family, model=model, local=local)


def parse_router_config(config: Mapping[str, Any]) -> RouterConfig:
    """Parse the top-level ``router:`` block (absent -> unconfigured RouterConfig).

    Opt-in: a config with no ``router`` key yields ``RouterConfig()`` (backend None),
    and :func:`select_backend` then returns the default path. A configured block must
    name a known backend and, for litellm-proxy, a ``base_url``.
    """
    raw = config.get("router")
    if raw is None:
        return RouterConfig()
    if not isinstance(raw, Mapping):
        raise RouterError("router must be a mapping")
    backend = raw.get("backend")
    if backend is None:
        return RouterConfig(local_only_classes=_local_only(raw))
    if backend != LITELLM_PROXY:
        raise RouterError(f"router.backend: unknown backend {backend!r}; only {LITELLM_PROXY!r}")
    base_url = raw.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise RouterError("router.base_url is required for the litellm-proxy backend")
    return RouterConfig(backend=backend, base_url=base_url, local_only_classes=_local_only(raw))


def _local_only(raw: Mapping[str, Any]) -> frozenset[str]:
    classes = raw.get("local_only_classes", [])
    if not (isinstance(classes, list) and all(isinstance(c, str) for c in classes)):
        raise RouterError("router.local_only_classes must be a list of strings")
    return frozenset(classes)


def select_backend(config: Mapping[str, Any]) -> HarnessBackend:
    """The active backend. Unconfigured -> :class:`DefaultBackend` (byte-identical path)."""
    rc = parse_router_config(config)
    if rc.backend == LITELLM_PROXY and rc.base_url is not None:
        return LiteLLMProxyBackend(base_url=rc.base_url)
    return DefaultBackend()


def _agents(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = config.get("agents", [])
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, Mapping)]
    if isinstance(raw, Mapping):  # mapping form: name -> body
        out: list[Mapping[str, Any]] = []
        for name, body in raw.items():
            if isinstance(body, Mapping):
                out.append({"name": name, **body})
        return out
    return []


def effective_route(config: Mapping[str, Any], agent: str) -> Route | None:
    """The resolved :class:`Route` an *agent* declares, or ``None`` if it names none.

    The SINGLE resolution point (core owns it, cli's question 20261001T135210): the
    cli doctor's "effective routing per agent" and the bridge dispatch read the same
    function, never each parsing the raw field. Both the array and mapping agent-
    declaration forms are supported.
    """
    for entry in _agents(config):
        if entry.get("name") == agent:
            raw = entry.get("route")
            return None if raw is None else _route_from_raw(raw, f"agents[{agent}]")
    return None


def route_leaves_tenant(target: BackendTarget) -> bool:
    """Whether *target* sends content outside the tenant (the guard's one question)."""
    return not target.local


def guard_route(
    rc: RouterConfig, target: BackendTarget, content_classes: frozenset[str] | set[str]
) -> str | None:
    """The sensitivity-guard refusal for *target* under *content_classes*, or ``None``.

    The predicate the bridge dispatch enforces (1.3): if the content carries any class
    in :attr:`RouterConfig.local_only_classes` and *target* leaves the tenant, return a
    refusal naming the class and target (nss — the guard states why). ``None`` means
    the route is admissible. Local targets are always admissible; a cloud route for
    guarded content is a conformance defect, so this never returns ``None`` for it.
    """
    if not route_leaves_tenant(target):
        return None
    guarded = sorted(rc.local_only_classes & set(content_classes))
    if not guarded:
        return None
    where = target.base_url or target.family
    return (
        f"route refused: content class {guarded[0]!r} is local-only but the route "
        f"target ({where}, family {target.family!r}) leaves the tenant"
    )


__all__ = [
    "DEFAULT_BACKEND",
    "LITELLM_PROXY",
    "BackendTarget",
    "DefaultBackend",
    "HarnessBackend",
    "LiteLLMProxyBackend",
    "Route",
    "RouterConfig",
    "RouterError",
    "effective_route",
    "guard_route",
    "parse_router_config",
    "route_leaves_tenant",
    "select_backend",
]
