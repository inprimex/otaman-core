"""OIDC token validation — Zitadel-anchored auth boundary.

Per ADR-010, otaman-bridge / otaman-runner / otaman-web validate JWTs
issued by a configured OIDC provider (default: Zitadel) at the network
boundary. This module is the shared validator used by all three services.

The validator:
- Fetches and caches the JWKS document for 5 minutes
- Verifies token signature against the matching key by ``kid``
- Verifies ``iss``, ``aud``, ``exp`` per RFC 7519 + RFC 8725
- Extracts roles from Zitadel's project-role claim
  ``urn:zitadel:iam:org:project:roles`` (a dict keyed by role)
- Optionally enforces a ``required_role`` per validator instance

The validator never raises on token validity errors — it always returns
``OIDCAuthResult(ok=False, error=...)`` so the daemon can turn that into
a 401 response. ``OIDCError`` is reserved for configuration / unrecoverable
JWKS-fetch problems.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger("otaman.core.auth_oidc")

ZITADEL_ROLES_CLAIM = "urn:zitadel:iam:org:project:roles"

_DEFAULT_JWKS_CACHE_TTL = 300.0  # 5 minutes


class OIDCError(RuntimeError):
    """Raised for configuration or unrecoverable provider failures.

    Token-invalidity is NOT an OIDCError — that returns
    ``OIDCAuthResult(ok=False, ...)`` instead.
    """


@dataclass(frozen=True)
class OIDCConfig:
    """Per-service OIDC configuration.

    Attributes:
        issuer: Expected ``iss`` claim, e.g.
            ``https://greenbin-otaman.example.com/auth``.
        audience: Expected ``aud`` claim — this service's Zitadel client id.
        jwks_uri: Where to fetch the JWKS document. If empty/None, the
            validator builds it from ``issuer`` by appending ``/.well-known/jwks``
            (Zitadel's default path).
        required_role: Optional role-string filter; ``None`` means any role
            (or no role) is acceptable. Pass e.g. ``"otaman:developer"`` to
            require it.
        algorithms: List of acceptable signing algorithms. Defaults to
            ``["RS256"]`` (Zitadel's default; matches the spec).
        leeway: Seconds of clock skew tolerance for ``exp`` / ``nbf``.
            Default 30s.
    """

    issuer: str
    audience: str
    jwks_uri: str | None = None
    required_role: str | None = None
    algorithms: tuple[str, ...] = ("RS256",)
    leeway: float = 30.0

    def effective_jwks_uri(self) -> str:
        """Where to fetch the JWKS document.

        If ``jwks_uri`` was set explicitly, use it. Otherwise default to
        Zitadel's actual JWKS path ``<issuer>/oauth/v2/keys`` -- Zitadel
        is otaman's reference IdP per the integration spec, and this
        path is what its ``/.well-known/openid-configuration`` advertises.
        Earlier the default was ``/.well-known/jwks`` which was a
        misreading of the OIDC spec; that path doesn't exist on Zitadel
        (returns 404), and the manual-test on 2026-05-15 caught it.

        For non-Zitadel IdPs, set ``jwks_uri`` explicitly. A future
        improvement is OIDC discovery: fetch ``/.well-known/openid-configuration``
        and use the ``jwks_uri`` field from that response.
        """
        if self.jwks_uri:
            return self.jwks_uri
        return f"{self.issuer.rstrip('/')}/oauth/v2/keys"


@dataclass(frozen=True)
class OIDCAuthResult:
    """Outcome of a token validation."""

    ok: bool
    user_id: str | None = None
    email: str | None = None
    roles: tuple[str, ...] = ()
    error: str | None = None


class OIDCValidator:
    """JWKS-backed JWT validator with a small in-process key cache."""

    def __init__(
        self,
        config: OIDCConfig,
        *,
        cache_ttl: float = _DEFAULT_JWKS_CACHE_TTL,
        jwks_fetcher=None,
        clock=None,
    ) -> None:
        """
        Args:
            config: required immutable per-service config
            cache_ttl: how long to trust the cached JWKS, seconds
            jwks_fetcher: optional ``f(url) -> dict`` for tests to bypass
                real HTTP fetch
            clock: optional ``f() -> float`` for tests to control time
        """
        self.config = config
        self.cache_ttl = cache_ttl
        self._jwks_fetcher = jwks_fetcher or _default_jwks_fetcher
        self._clock = clock or time.time
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0.0

    # ---- Public API -------------------------------------------------

    def validate(self, bearer_header: str | None) -> OIDCAuthResult:
        """Parse + verify a ``Authorization: Bearer <token>`` header.

        Returns an ``OIDCAuthResult`` — never raises for token-validity
        problems. Raises ``OIDCError`` only for unrecoverable JWKS-fetch
        failure (which the caller should turn into a 5xx, not a 401).
        """
        if not bearer_header:
            return OIDCAuthResult(ok=False, error="missing Authorization header")
        if not bearer_header.startswith("Bearer "):
            return OIDCAuthResult(ok=False, error="Authorization is not Bearer")
        token = bearer_header[len("Bearer ") :].strip()
        if not token:
            return OIDCAuthResult(ok=False, error="empty bearer token")

        # Defer the import so callers without OIDC enabled don't pay the cost.
        import jwt

        # Resolve which key to use via kid.
        try:
            unverified = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            return OIDCAuthResult(ok=False, error=f"malformed token: {exc}")
        kid = unverified.get("kid")
        if not kid:
            return OIDCAuthResult(ok=False, error="token header missing kid")

        try:
            key = self._key_for_kid(kid)
        except _KidNotFound:
            return OIDCAuthResult(ok=False, error=f"kid {kid!r} not in JWKS")

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.config.algorithms),
                audience=self.config.audience,
                issuer=self.config.issuer,
                leeway=self.config.leeway,
            )
        except jwt.InvalidTokenError as exc:
            return OIDCAuthResult(ok=False, error=str(exc))

        roles = _extract_roles(claims)
        if self.config.required_role and self.config.required_role not in roles:
            return OIDCAuthResult(
                ok=False,
                error=f"missing required role {self.config.required_role!r}",
                user_id=claims.get("sub"),
                roles=tuple(roles),
            )

        return OIDCAuthResult(
            ok=True,
            user_id=claims.get("sub"),
            email=claims.get("email"),
            roles=tuple(roles),
        )

    # ---- JWKS handling ----------------------------------------------

    def _jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """Return the JWKS document, refreshing the cache if stale.

        Refresh failures use the stale cache if available; otherwise raise.
        """
        now = self._clock()
        if (
            not force_refresh
            and self._jwks_cache is not None
            and (now - self._jwks_fetched_at) < self.cache_ttl
        ):
            return self._jwks_cache
        try:
            fresh = self._jwks_fetcher(self.config.effective_jwks_uri())
        except OIDCError:
            if self._jwks_cache is not None:
                _log.warning("JWKS refresh failed; using stale cache")
                return self._jwks_cache
            raise
        self._jwks_cache = fresh
        self._jwks_fetched_at = now
        return fresh

    def _key_for_kid(self, kid: str):
        """Find the JWK matching kid; refresh JWKS once on cache miss."""
        import jwt

        def _find(jwks: dict[str, Any]):
            for k in jwks.get("keys") or ():
                if k.get("kid") == kid:
                    return jwt.algorithms.RSAAlgorithm.from_jwk(k)
            return None

        # First try the cache; on miss, refresh once.
        cache = self._jwks(force_refresh=False)
        found = _find(cache)
        if found is not None:
            return found
        # Key rotation case — refresh and retry.
        fresh = self._jwks(force_refresh=True)
        found = _find(fresh)
        if found is None:
            raise _KidNotFound(kid)
        return found


# ---- Helpers ------------------------------------------------------------


class _KidNotFound(Exception):
    pass


def _extract_roles(claims: dict[str, Any]) -> list[str]:
    """Extract role keys from Zitadel's project-role claim shape.

    Zitadel emits roles under a project-scoped claim that includes the
    project resource id:

        "urn:zitadel:iam:org:project:<PROJECT_ID>:roles": {
            "otaman:developer": {"<org-id>": "<org-domain>"},
            "otaman:viewer":    {"<org-id>": "<org-domain>"},
        }

    Discovered empirically against Zitadel v2.66 (2026-05-15 smoke test
    on Greenbin pilot dogfood). Older Zitadel versions also recognised
    the legacy claim name without the project id; we accept both for
    forward / backward compatibility.

    Multiple project claims may appear in one token (one per project the
    subject has roles in). Returns the union of role keys across all of
    them. Returns ``[]`` if no role claim is present or every claim is
    malformed.
    """
    roles: list[str] = []
    for claim_name, value in claims.items():
        # Legacy + project-scoped names both start with the same prefix and
        # end with ':roles'. The middle is empty (legacy) or the project id.
        if not claim_name.startswith("urn:zitadel:iam:org:project:"):
            continue
        if not claim_name.endswith(":roles") and claim_name != ZITADEL_ROLES_CLAIM:
            continue
        if not isinstance(value, dict):
            _log.debug("roles claim %s is not a dict; ignoring", claim_name)
            continue
        for role_key in value:
            if role_key not in roles:
                roles.append(role_key)
    return roles


def _default_jwks_fetcher(url: str) -> dict[str, Any]:
    """Fetch a JWKS document via HTTPS.

    Sets an explicit User-Agent — Cloudflare's Bot Fight Mode (and similar
    TLS-terminating proxies) block urllib's default ``Python-urllib/3.x``
    with 403. Any deployment fronting the IdP with a CDN/WAF hits this
    on every token validation. Caught 2026-05-19 during otaman-bridge's
    Cloudflare Tunnel migration.

    Raises OIDCError on any network / parse failure.
    """
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "otaman-core-oidc/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OIDCError(f"JWKS fetch failed for {url}: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise OIDCError(f"JWKS response is not valid JSON: {exc}") from exc


__all__ = [
    "OIDCConfig",
    "OIDCAuthResult",
    "OIDCValidator",
    "OIDCError",
    "ZITADEL_ROLES_CLAIM",
]
