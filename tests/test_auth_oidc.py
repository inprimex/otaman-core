"""Tests for OIDCValidator.

Uses ``cryptography`` to generate a real RSA keypair, builds a JWKS dict,
signs JWTs with it, and runs them through the validator. No real HTTP
calls — JWKS fetch is injected via the validator's ``jwks_fetcher``.
"""

from __future__ import annotations

import time

import pytest

jwt = pytest.importorskip("jwt", reason="PyJWT not installed")
crypto_serialization = pytest.importorskip(
    "cryptography.hazmat.primitives.serialization",
    reason="cryptography not installed",
)
from cryptography.hazmat.primitives.asymmetric import rsa

from otaman_core.auth_oidc import (
    ZITADEL_ROLES_CLAIM,
    OIDCConfig,
    OIDCError,
    OIDCValidator,
    _extract_roles,
)

# ---- Test infrastructure ------------------------------------------------


def _jwk_from_public_key(public_key, kid: str) -> dict:
    """Convert a cryptography RSA public key to JWK dict format."""
    import base64

    numbers = public_key.public_numbers()
    n_bytes = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e_bytes = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": b64url(n_bytes),
        "e": b64url(e_bytes),
    }


@pytest.fixture
def keypair():
    """Fresh RSA keypair per-test; not generated once because tests mutate kids."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=crypto_serialization.Encoding.PEM,
        format=crypto_serialization.PrivateFormat.PKCS8,
        encryption_algorithm=crypto_serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    return {
        "private_pem": private_pem,
        "private_key": private_key,
        "public_key": public_key,
    }


@pytest.fixture
def jwks(keypair):
    """JWKS dict with one key under kid='test-key-1'."""
    return {"keys": [_jwk_from_public_key(keypair["public_key"], "test-key-1")]}


@pytest.fixture
def config():
    return OIDCConfig(
        issuer="https://otaman.example.com/auth",
        audience="otaman-runner",
    )


def _make_token(
    private_pem: bytes,
    *,
    kid: str = "test-key-1",
    iss: str = "https://otaman.example.com/auth",
    aud: str | list[str] = "otaman-runner",
    sub: str = "user-uuid-1",
    email: str = "alice@example.com",
    exp_in: float = 3600,
    extra_claims: dict | None = None,
    algorithm: str = "RS256",
) -> str:
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time() + exp_in),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        private_pem,
        algorithm=algorithm,
        headers={"kid": kid},
    )


# ---- OIDCConfig --------------------------------------------------------


class TestOIDCConfig:
    def test_effective_jwks_uri_from_explicit(self):
        cfg = OIDCConfig(
            issuer="https://x", audience="y",
            jwks_uri="https://override.example/jwks",
        )
        assert cfg.effective_jwks_uri() == "https://override.example/jwks"

    def test_effective_jwks_uri_from_issuer(self):
        cfg = OIDCConfig(issuer="https://otaman.example.com/auth", audience="y")
        assert cfg.effective_jwks_uri() == "https://otaman.example.com/auth/oauth/v2/keys"

    def test_strips_trailing_slash_on_issuer(self):
        cfg = OIDCConfig(issuer="https://otaman.example.com/auth/", audience="y")
        assert cfg.effective_jwks_uri() == "https://otaman.example.com/auth/oauth/v2/keys"


# ---- Happy paths -------------------------------------------------------


class TestValidation:
    def test_valid_token_accepted(self, config, keypair, jwks):
        token = _make_token(keypair["private_pem"])
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is True
        assert result.user_id == "user-uuid-1"
        assert result.email == "alice@example.com"
        assert result.roles == ()

    def test_token_with_roles_extracted(self, config, keypair, jwks):
        token = _make_token(
            keypair["private_pem"],
            extra_claims={
                ZITADEL_ROLES_CLAIM: {
                    "otaman:developer": {"proj-1": "Greenbin"},
                    "otaman:approver": {"proj-1": "Greenbin"},
                },
            },
        )
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is True
        assert set(result.roles) == {"otaman:developer", "otaman:approver"}

    def test_required_role_satisfied(self, keypair, jwks):
        cfg = OIDCConfig(
            issuer="https://otaman.example.com/auth",
            audience="otaman-runner",
            required_role="otaman:developer",
        )
        token = _make_token(
            keypair["private_pem"],
            extra_claims={
                ZITADEL_ROLES_CLAIM: {"otaman:developer": {"proj-1": "Greenbin"}},
            },
        )
        validator = OIDCValidator(cfg, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is True


# ---- Bad inputs --------------------------------------------------------


class TestBadInputs:
    def test_missing_header(self, config):
        validator = OIDCValidator(config, jwks_fetcher=lambda url: {"keys": []})
        result = validator.validate(None)
        assert result.ok is False
        assert "missing" in result.error.lower()

    def test_empty_header(self, config):
        validator = OIDCValidator(config, jwks_fetcher=lambda url: {"keys": []})
        result = validator.validate("")
        assert result.ok is False

    def test_non_bearer_scheme(self, config):
        validator = OIDCValidator(config, jwks_fetcher=lambda url: {"keys": []})
        result = validator.validate("Basic abc")
        assert result.ok is False
        assert "not bearer" in result.error.lower()

    def test_bearer_with_empty_token(self, config):
        validator = OIDCValidator(config, jwks_fetcher=lambda url: {"keys": []})
        result = validator.validate("Bearer ")
        assert result.ok is False

    def test_malformed_token(self, config):
        validator = OIDCValidator(config, jwks_fetcher=lambda url: {"keys": []})
        result = validator.validate("Bearer not-a-real-jwt")
        assert result.ok is False
        assert "malformed" in result.error.lower()


# ---- Signature / claim verification ------------------------------------


class TestVerification:
    def test_kid_not_in_jwks(self, config, keypair, jwks):
        token = _make_token(keypair["private_pem"], kid="unknown-kid")
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "unknown-kid" in result.error

    def test_kid_missing_from_token_header(self, config, keypair, jwks):
        # Manually craft a token without kid in header — sign with private key
        token = jwt.encode(
            {
                "iss": config.issuer,
                "aud": config.audience,
                "sub": "u",
                "exp": int(time.time() + 60),
            },
            keypair["private_pem"],
            algorithm="RS256",
            # no headers={'kid': ...}
        )
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "kid" in result.error

    def test_wrong_issuer_rejected(self, config, keypair, jwks):
        token = _make_token(
            keypair["private_pem"], iss="https://other-issuer.example/",
        )
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        # PyJWT error message contains "issuer"
        assert "issuer" in result.error.lower() or "iss" in result.error.lower()

    def test_wrong_audience_rejected(self, config, keypair, jwks):
        token = _make_token(keypair["private_pem"], aud="some-other-client")
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "audience" in result.error.lower() or "aud" in result.error.lower()

    def test_expired_token_rejected(self, config, keypair, jwks):
        token = _make_token(keypair["private_pem"], exp_in=-100)  # already expired
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "expir" in result.error.lower()

    def test_required_role_missing(self, keypair, jwks):
        cfg = OIDCConfig(
            issuer="https://otaman.example.com/auth",
            audience="otaman-runner",
            required_role="otaman:admin",
        )
        token = _make_token(
            keypair["private_pem"],
            extra_claims={
                ZITADEL_ROLES_CLAIM: {"otaman:viewer": {"proj-1": "Greenbin"}},
            },
        )
        validator = OIDCValidator(cfg, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "otaman:admin" in result.error
        # User id is exposed even on failure for audit
        assert result.user_id == "user-uuid-1"

    def test_signature_tampering_rejected(self, config, keypair, jwks):
        token = _make_token(keypair["private_pem"])
        # Flip the first signature byte deterministically. Use a char
        # different from sig[0] so the tamper is real for any fresh key.
        head, payload, sig = token.split(".")
        flip = "B" if sig[0] == "A" else "A"
        tampered = f"{head}.{payload}.{flip}{sig[1:]}"
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {tampered}")
        assert result.ok is False


# ---- JWKS cache + rotation ---------------------------------------------


class TestJWKSCache:
    def test_jwks_fetched_only_once_within_ttl(self, config, keypair, jwks):
        calls = []

        def counting_fetcher(url):
            calls.append(url)
            return jwks

        validator = OIDCValidator(
            config, jwks_fetcher=counting_fetcher, cache_ttl=300,
        )
        token = _make_token(keypair["private_pem"])
        validator.validate(f"Bearer {token}")
        validator.validate(f"Bearer {token}")
        validator.validate(f"Bearer {token}")
        # First validate triggered one fetch; subsequent use cache.
        assert len(calls) == 1

    def test_jwks_refetched_after_ttl(self, config, keypair, jwks):
        clock_value = [1000.0]

        def fake_clock():
            return clock_value[0]

        calls = []

        def counting_fetcher(url):
            calls.append(url)
            return jwks

        validator = OIDCValidator(
            config,
            jwks_fetcher=counting_fetcher,
            cache_ttl=300,
            clock=fake_clock,
        )
        token = _make_token(keypair["private_pem"])
        validator.validate(f"Bearer {token}")
        # Advance past TTL
        clock_value[0] += 400
        validator.validate(f"Bearer {token}")
        assert len(calls) == 2

    def test_unknown_kid_triggers_refresh(self, config, keypair):
        """Key rotation: kid not in cached JWKS triggers a single refresh."""
        # First JWKS has one key; rotated JWKS will have a different key.
        new_pem = keypair["private_pem"]  # same key, different kid
        original = {"keys": [_jwk_from_public_key(keypair["public_key"], "old-kid")]}
        rotated = {"keys": [_jwk_from_public_key(keypair["public_key"], "new-kid")]}

        fetch_count = [0]

        def rotating_fetcher(url):
            fetch_count[0] += 1
            return original if fetch_count[0] == 1 else rotated

        validator = OIDCValidator(config, jwks_fetcher=rotating_fetcher)
        # Token signed with kid='new-kid' (not in initial cache → forces refresh)
        token = _make_token(new_pem, kid="new-kid")
        result = validator.validate(f"Bearer {token}")
        assert result.ok is True, result.error
        # Initial fetch + refresh = 2
        assert fetch_count[0] == 2

    def test_jwks_fetch_failure_with_no_cache_raises(self, config, keypair):
        """OIDCError propagates when JWKS fetch fails and no cache exists.

        Use a properly-signed token so validation reaches the JWKS-fetch
        step. A malformed token short-circuits before fetch and never
        triggers this path.
        """
        token = _make_token(keypair["private_pem"])

        def failing_fetcher(url):
            raise OIDCError("network down")

        validator = OIDCValidator(config, jwks_fetcher=failing_fetcher)
        with pytest.raises(OIDCError, match="network down"):
            validator.validate(f"Bearer {token}")

    def test_jwks_fetch_failure_falls_back_to_stale_cache(
        self, config, keypair, jwks
    ):
        fetch_count = [0]

        def flaky_fetcher(url):
            fetch_count[0] += 1
            if fetch_count[0] == 1:
                return jwks
            raise OIDCError("network blip")

        clock_value = [1000.0]

        def fake_clock():
            return clock_value[0]

        validator = OIDCValidator(
            config,
            jwks_fetcher=flaky_fetcher,
            cache_ttl=300,
            clock=fake_clock,
        )
        token = _make_token(keypair["private_pem"])
        # First validate: succeeds, populates cache
        assert validator.validate(f"Bearer {token}").ok is True
        # Advance past TTL — refresh tries and fails; stale cache used
        clock_value[0] += 400
        result = validator.validate(f"Bearer {token}")
        assert result.ok is True


# ---- _extract_roles defensive parsing ----------------------------------


class TestExtractRoles:
    def test_present_dict(self):
        claims = {ZITADEL_ROLES_CLAIM: {"otaman:developer": {"p": "P"}}}
        assert _extract_roles(claims) == ["otaman:developer"]

    def test_absent_claim_returns_empty(self):
        assert _extract_roles({}) == []

    def test_non_dict_claim_returns_empty(self):
        assert _extract_roles({ZITADEL_ROLES_CLAIM: ["not", "a", "dict"]}) == []

    def test_null_claim_returns_empty(self):
        assert _extract_roles({ZITADEL_ROLES_CLAIM: None}) == []

    def test_project_scoped_claim_shape(self):
        """Real Zitadel emits roles under a project-scoped key:
        urn:zitadel:iam:org:project:<PROJECT_ID>:roles
        Verified empirically against Zitadel v2.66 (2026-05-15 smoke)."""
        claims = {
            "urn:zitadel:iam:org:project:372944870769164291:roles": {
                "otaman:developer": {"<org-id>": "<org-domain>"},
                "otaman:viewer": {"<org-id>": "<org-domain>"},
            },
        }
        assert sorted(_extract_roles(claims)) == ["otaman:developer", "otaman:viewer"]

    def test_multiple_project_scoped_claims_unioned(self):
        """A token may carry roles for multiple projects; union them."""
        claims = {
            "urn:zitadel:iam:org:project:111:roles": {"otaman:developer": {"o": "x"}},
            "urn:zitadel:iam:org:project:222:roles": {"otaman:viewer": {"o": "x"}},
        }
        assert sorted(_extract_roles(claims)) == ["otaman:developer", "otaman:viewer"]

    def test_unrelated_urn_claims_ignored(self):
        claims = {
            "urn:zitadel:iam:org:project:111:something-else": {"x": 1},
            "urn:other:prefix:roles": {"y": 2},
        }
        assert _extract_roles(claims) == []

    def test_legacy_claim_still_works(self):
        """Older Zitadel (or future Zitadel reverting) used the bare claim."""
        claims = {ZITADEL_ROLES_CLAIM: {"otaman:admin": {"o": "x"}}}
        assert _extract_roles(claims) == ["otaman:admin"]


# ---- Security hardening (added 2026-05-15) -----------------------------


class TestSecurityHardening:
    """Algorithm-confusion, leeway, and multi-audience tests.

    Covers security-relevant configuration boundaries that the earlier
    suite didn't exercise: explicit algorithm enforcement (a textbook
    attack class), the leeway field, and the multi-audience case allowed
    by RFC 7519 sec. 4.1.3.
    """

    def test_hs256_token_rejected_when_only_rs256_allowed(self, config, keypair, jwks):
        """HS256-signed token must NOT be accepted by an RS256-only validator.

        Algorithm-confusion attacks (e.g. signing with the public key's
        bytes as a HMAC secret) are blocked at the algorithm-allowlist layer.
        """
        token = jwt.encode(
            {
                "iss": config.issuer,
                "aud": config.audience,
                "sub": "u",
                "exp": int(time.time() + 60),
            },
            "any-symmetric-secret",
            algorithm="HS256",
            headers={"kid": "test-key-1"},
        )
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "alg" in result.error.lower() or "algorithm" in result.error.lower()

    def test_alg_none_rejected(self, config, jwks):
        """Token with alg=none must be rejected. PyJWT requires opt-in
        to permit none; this guards against a config drift that allows it."""
        token = jwt.encode(
            {
                "iss": config.issuer,
                "aud": config.audience,
                "sub": "u",
                "exp": int(time.time() + 60),
            },
            "",
            algorithm="none",
            headers={"kid": "test-key-1"},
        )
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False

    def test_leeway_allows_recently_expired_token(self, keypair, jwks):
        """Token expired by less than leeway seconds must pass.

        Accommodates clock skew between issuer and validator.
        """
        cfg = OIDCConfig(
            issuer="https://otaman.example.com/auth",
            audience="otaman-runner",
            leeway=30.0,
        )
        token = _make_token(keypair["private_pem"], exp_in=-10)
        validator = OIDCValidator(cfg, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is True, f"unexpected error: {result.error}"

    def test_leeway_does_not_extend_beyond_window(self, keypair, jwks):
        """Token expired by more than leeway seconds must still fail."""
        cfg = OIDCConfig(
            issuer="https://otaman.example.com/auth",
            audience="otaman-runner",
            leeway=30.0,
        )
        token = _make_token(keypair["private_pem"], exp_in=-100)
        validator = OIDCValidator(cfg, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "expir" in result.error.lower()

    def test_aud_list_containing_expected_accepted(self, config, keypair, jwks):
        """RFC 7519 sec. 4.1.3: aud may be str OR list.

        Token with aud=[a, b] where 'a' matches config.audience must pass.
        """
        token = _make_token(
            keypair["private_pem"],
            aud=[config.audience, "some-other-service"],
        )
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is True, f"unexpected error: {result.error}"

    def test_aud_list_not_containing_expected_rejected(self, config, keypair, jwks):
        """Multi-aud token where none of the audiences match config must fail."""
        token = _make_token(
            keypair["private_pem"],
            aud=["service-a", "service-b"],
        )
        validator = OIDCValidator(config, jwks_fetcher=lambda url: jwks)
        result = validator.validate(f"Bearer {token}")
        assert result.ok is False
        assert "audience" in result.error.lower() or "aud" in result.error.lower()
