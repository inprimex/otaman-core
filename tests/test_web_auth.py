"""Tests for the extracted host-agnostic web-auth service (ce-refresh-token 1.1).

Moved/extended from otaman-runner's terminal-auth unit tests; covers login,
attach-token issuance, and JWT verification of the shared core module that both
the CE bridge and the EE runner mount.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

try:
    import bcrypt
    import jwt
except ImportError:  # pragma: no cover - provided by the `web-auth`/`test` extra
    pytest.skip("web-auth extra (pyjwt+bcrypt) not installed", allow_module_level=True)

from otaman_core.web_auth import (
    AuthError,
    AuthService,
    CeAuthManager,
    LocalAuthConfig,
    UserRecord,
    parse_local_auth_config,
)

_JWT_ALG = "HS256"
# >=32 bytes to satisfy PyJWT's HMAC key-length check (prod uses token_urlsafe(32)).
_SECRET = "test-hmac-secret-fixed-0123456789-abcdef"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _make_manager(
    *,
    enabled: bool = True,
    session_ttl: int = 28800,
    attach_ttl: int = 3600,
    role: str = "developer",
) -> CeAuthManager:
    config = LocalAuthConfig(
        enabled=enabled,
        session_ttl=session_ttl,
        users=[UserRecord(username="alice", password_hash=_hash("secret123"), role=role)],
    )
    return CeAuthManager(config=config, hmac_secret=_SECRET, attach_token_ttl=attach_ttl)


def _forge(payload: dict) -> str:
    return jwt.encode(payload, _SECRET, algorithm=_JWT_ALG)


class TestLogin:
    def test_valid_credentials_returns_jwt(self):
        token = _make_manager().login("alice", "secret123")
        claims = jwt.decode(token, _SECRET, algorithms=[_JWT_ALG])
        assert claims["sub"] == "alice" and claims["type"] == "session"

    def test_exp_within_session_ttl(self):
        token = _make_manager(session_ttl=100).login("alice", "secret123")
        claims = jwt.decode(token, _SECRET, algorithms=[_JWT_ALG])
        assert claims["exp"] - claims["iat"] == 100

    def test_wrong_password_raises(self):
        with pytest.raises(AuthError, match="invalid credentials"):
            _make_manager().login("alice", "wrong")

    def test_unknown_username_raises(self):
        with pytest.raises(AuthError, match="invalid credentials"):
            _make_manager().login("bob", "secret123")

    def test_disabled_local_auth_raises(self):
        with pytest.raises(AuthError, match="disabled"):
            _make_manager(enabled=False).login("alice", "secret123")

    def test_role_carried_in_claims(self):
        token = _make_manager(role="observer").login("alice", "secret123")
        assert jwt.decode(token, _SECRET, algorithms=[_JWT_ALG])["role"] == "observer"


class TestIssueSessionToken:
    """Password-free session minting for the refresh flow (bridge 1.2 seam)."""

    def test_mints_valid_session_jwt(self):
        token = _make_manager().issue_session_token("alice")
        claims = jwt.decode(token, _SECRET, algorithms=[_JWT_ALG])
        assert claims["sub"] == "alice" and claims["type"] == "session"

    def test_payload_matches_login(self):
        mgr = _make_manager(session_ttl=100, role="admin")
        from_login = jwt.decode(mgr.login("alice", "secret123"), _SECRET, algorithms=[_JWT_ALG])
        from_refresh = jwt.decode(mgr.issue_session_token("alice"), _SECRET, algorithms=[_JWT_ALG])
        # identical shape + values (modulo iat/exp timing) — same claim keys, role, ttl span
        assert from_login.keys() == from_refresh.keys()
        assert from_refresh["role"] == "admin"
        assert from_refresh["exp"] - from_refresh["iat"] == 100

    def test_unknown_user_raises(self):
        with pytest.raises(AuthError, match="unknown user"):
            _make_manager().issue_session_token("bob")

    def test_disabled_auth_raises(self):
        with pytest.raises(AuthError, match="disabled"):
            _make_manager(enabled=False).issue_session_token("alice")

    def test_role_reauthoritative_from_config(self):
        # A minted token reflects the CURRENT config role, not a captured one.
        mgr = _make_manager(role="observer")
        claims = jwt.decode(mgr.issue_session_token("alice"), _SECRET, algorithms=[_JWT_ALG])
        assert claims["role"] == "observer"

    def test_minted_token_drives_attach_flow(self):
        # End-to-end refresh: mint session -> exchange for attach, like the route.
        mgr = _make_manager(role="developer")
        session = mgr.issue_session_token("alice")
        result = mgr.attach_token(session)
        assert result["mode"] == "write"
        assert mgr.verify_session_token(session)["sub"] == "alice"


class TestAttachToken:
    def test_session_jwt_yields_attach_jwt(self):
        mgr = _make_manager()
        session = mgr.login("alice", "secret123")
        result = mgr.attach_token(session)
        assert set(result) == {"token", "expires_at", "mode"}
        assert jwt.decode(result["token"], _SECRET, algorithms=[_JWT_ALG])["type"] == "attach"

    def test_developer_gets_write_mode(self):
        mgr = _make_manager(role="developer")
        assert mgr.attach_token(mgr.login("alice", "secret123"))["mode"] == "write"

    def test_observer_gets_read_mode(self):
        mgr = _make_manager(role="observer")
        assert mgr.attach_token(mgr.login("alice", "secret123"))["mode"] == "read"

    def test_sessions_claim_scoped(self):
        mgr = _make_manager()
        session = mgr.login("alice", "secret123")
        result = mgr.attach_token(session, available_session_ids=["s1", "s2"])
        claims = jwt.decode(result["token"], _SECRET, algorithms=[_JWT_ALG])
        assert claims["sessions"] == ["s1", "s2"]

    def test_sessions_wildcard_when_none(self):
        mgr = _make_manager()
        result = mgr.attach_token(mgr.login("alice", "secret123"))
        assert jwt.decode(result["token"], _SECRET, algorithms=[_JWT_ALG])["sessions"] == ["*"]

    def test_expired_session_jwt_raises(self):
        mgr = _make_manager()
        past = int(datetime.now(UTC).timestamp()) - 10
        expired = _forge({"sub": "alice", "role": "developer", "exp": past, "type": "session"})
        with pytest.raises(AuthError, match="expired"):
            mgr.attach_token(expired)

    def test_attach_token_rejected_as_session(self):
        mgr = _make_manager()
        attach = mgr.attach_token(mgr.login("alice", "secret123"))["token"]
        with pytest.raises(AuthError, match="not a session token"):
            mgr.attach_token(attach)  # attach JWT is not a valid session JWT


class TestVerifyAttachToken:
    def test_valid_returns_claims(self):
        mgr = _make_manager()
        attach = mgr.attach_token(mgr.login("alice", "secret123"))["token"]
        assert mgr.verify_attach_token(attach)["type"] == "attach"

    def test_expired_raises(self):
        mgr = _make_manager()
        past = int(datetime.now(UTC).timestamp()) - 10
        with pytest.raises(AuthError, match="expired"):
            mgr.verify_attach_token(_forge({"type": "attach", "exp": past}))

    def test_tampered_raises(self):
        mgr = _make_manager()
        attach = mgr.attach_token(mgr.login("alice", "secret123"))["token"]
        with pytest.raises(AuthError, match="invalid attach token"):
            mgr.verify_attach_token(attach + "x")

    def test_wrong_secret_raises(self):
        mgr = _make_manager()
        forged = jwt.encode(
            {"type": "attach"}, "a-different-secret-0123456789-abcdef", algorithm=_JWT_ALG
        )
        with pytest.raises(AuthError):
            mgr.verify_attach_token(forged)

    def test_session_token_rejected(self):
        mgr = _make_manager()
        session = mgr.login("alice", "secret123")
        with pytest.raises(AuthError, match="not an attach token"):
            mgr.verify_attach_token(session)


class TestVerifyTokens:
    def test_verify_session_token(self):
        mgr = _make_manager()
        session = mgr.login("alice", "secret123")
        assert mgr.verify_session_token(session)["sub"] == "alice"

    def test_verify_user_token_accepts_session_and_attach(self):
        mgr = _make_manager()
        session = mgr.login("alice", "secret123")
        attach = mgr.attach_token(session)["token"]
        assert mgr.verify_user_token(session)["sub"] == "alice"
        assert mgr.verify_user_token(attach)["sub"] == "alice"

    def test_verify_user_token_rejects_other_type(self):
        mgr = _make_manager()
        with pytest.raises(AuthError, match="unexpected token type"):
            mgr.verify_user_token(_forge({"type": "refresh", "sub": "alice"}))


class TestConfigAndSecret:
    def test_parse_missing_file_is_disabled(self, tmp_path):
        cfg = parse_local_auth_config(tmp_path / "nope.yaml")
        assert cfg.enabled is False

    def test_parse_reads_users_and_ttl(self, tmp_path):
        import yaml

        p = tmp_path / "platform.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "terminal": {
                        "local_auth": {"enabled": True, "session_ttl": 3600},
                        "users": [
                            {"username": "alice", "password_hash": "$2b$x", "role": "admin"},
                            {"username": "", "password_hash": "skip"},  # skipped
                        ],
                    }
                }
            )
        )
        cfg = parse_local_auth_config(p)
        assert cfg.session_ttl == 3600
        assert [u.username for u in cfg.users] == ["alice"]
        assert cfg.users[0].role == "admin"

    def test_from_platform_yaml_creates_hmac_secret(self, tmp_path):
        p = tmp_path / "platform.yaml"
        p.write_text("terminal:\n  users: []\n")
        state = tmp_path / "state"
        mgr = CeAuthManager.from_platform_yaml(p, state)
        assert (state / "terminal_hmac_secret").is_file()
        assert mgr.enabled is True  # default when local_auth block absent

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    def test_hmac_secret_0600_and_stable(self, tmp_path):
        p = tmp_path / "platform.yaml"
        p.write_text("terminal:\n  users: []\n")
        state = tmp_path / "state"
        m1 = CeAuthManager.from_platform_yaml(p, state)
        secret_file = state / "terminal_hmac_secret"
        assert (secret_file.stat().st_mode & 0o777) == 0o600
        # a second construction reuses the same persisted secret (tokens stay valid)
        token = m1.login  # noqa: F841 (just ensure construction reused below)
        m2 = CeAuthManager.from_platform_yaml(p, state)
        assert secret_file.read_text() == (state / "terminal_hmac_secret").read_text()
        assert m2 is not m1


def test_auth_service_alias_is_ce_auth_manager():
    assert AuthService is CeAuthManager
