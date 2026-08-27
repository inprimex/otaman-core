"""Host-agnostic CE web-auth service (login / attach-token / JWT issue+verify).

The single, shared implementation of the terminal-auth-rbac two-token flow.
It lives here in otaman-core so ONE implementation serves both hosts: the
bridge mounts it as the runner-free CE surface, and the EE runner re-mounts the
same module — there SHALL NOT be two auth implementations (ce-web-auth spec).

Two-token flow:

  1. POST /api/auth/login  {username, password}
     -> verify bcrypt hash from platform.yaml
     -> issue session JWT (sub, role, exp)

  2. POST /api/terminal/attach-token  Authorization: Bearer <session_jwt>
     -> verify session JWT HMAC signature + exp
     -> derive scope: role -> mode (read|write) + sessions list
     -> issue attach JWT (sub, sessions, mode, exp)

  3. WS upgrade  ?token=<attach_jwt>
     -> verify attach JWT signature + exp + session scope
     -> enforce mode: read tokens -> keystrokes silently dropped

EE path (OIDC): the caller presents an upstream bearer token at step 2 instead
of a session JWT; the attach JWT format is identical, only the upstream
credential differs. EE upstream verification is a separate path not implemented
here.

HMAC secret: persisted in ``state_dir/terminal_hmac_secret`` (0600) so attach
JWTs survive host restarts within their TTL. Never logged or returned in API
responses.

Requires the ``web-auth`` extra (PyJWT + bcrypt).

Provenance & licensing
----------------------
Extracted from otaman-runner ``src/otaman_runner/terminal/auth.py`` (the
terminal-auth-rbac implementation) as part of ce-refresh-token 1.1. That code
was EE-runner code; Roman blessed relicensing it into otaman-core under
AGPL-3.0-only (the core license) on 2026-08-27, so one host-agnostic module
serves both the CE bridge and the EE runner. Behavior is preserved verbatim
from the runner implementation; only the host framing (logger namespace) is
neutralized.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jwt  # PyJWT (from the `web-auth` extra)

_log = logging.getLogger("otaman.web_auth")

# Role -> WS mode mapping
_ROLE_MODE: dict[str, str] = {
    "developer": "write",
    "admin": "write",
    "observer": "read",
}

# Algorithm used for all JWTs issued by this module
_JWT_ALG = "HS256"

# Secret file name inside state_dir
_SECRET_FILE = "terminal_hmac_secret"


@dataclass(frozen=True)
class UserRecord:
    username: str
    password_hash: str  # bcrypt hash
    role: str  # developer | observer | admin


@dataclass
class LocalAuthConfig:
    enabled: bool = True
    session_ttl: int = 28800  # seconds (8 h)
    users: list[UserRecord] = field(default_factory=list)


def _load_or_create_hmac_secret(state_dir: Path) -> str:
    """Return the persistent HMAC secret, creating it (0600) on first run."""
    secret_file = state_dir / _SECRET_FILE
    if secret_file.is_file():
        secret = secret_file.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    secret = secrets.token_urlsafe(32)
    state_dir.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(secret, encoding="utf-8")
    secret_file.chmod(0o600)
    _log.info("generated new terminal HMAC secret: %s", secret_file)
    return secret


def parse_local_auth_config(platform_yaml_path: Path) -> LocalAuthConfig:
    """Read ``terminal.local_auth`` + ``terminal.users`` from platform.yaml.

    Returns a default (disabled) config if the file is missing or the terminal
    block is absent — safe to call unconditionally at startup.
    """
    try:
        import yaml

        with open(platform_yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return LocalAuthConfig(enabled=False)
    except Exception as exc:
        _log.warning("failed to parse platform.yaml for CE auth: %s", exc)
        return LocalAuthConfig(enabled=False)

    terminal = data.get("terminal", {})
    local_auth = terminal.get("local_auth", {})
    enabled = local_auth.get("enabled", True)
    session_ttl = int(local_auth.get("session_ttl", 28800))

    users = []
    for entry in terminal.get("users", []):
        username = entry.get("username", "").strip()
        password_hash = entry.get("password_hash", "").strip()
        role = entry.get("role", "observer").strip()
        if username and password_hash:
            users.append(UserRecord(username=username, password_hash=password_hash, role=role))

    return LocalAuthConfig(enabled=enabled, session_ttl=session_ttl, users=users)


class CeAuthManager:
    """Handles CE (local accounts) login and attach-token issuance.

    Host-agnostic: constructed from a platform.yaml path + a state dir, with no
    host-specific coupling, so the bridge (CE) and runner (EE) both mount it.
    """

    def __init__(
        self,
        config: LocalAuthConfig,
        hmac_secret: str,
        attach_token_ttl: int = 3600,
    ) -> None:
        self._config = config
        self._secret = hmac_secret
        self._attach_ttl = attach_token_ttl

    @classmethod
    def from_platform_yaml(
        cls,
        platform_yaml_path: Path,
        state_dir: Path,
        *,
        attach_token_ttl: int = 3600,
    ) -> CeAuthManager:
        config = parse_local_auth_config(platform_yaml_path)
        secret = _load_or_create_hmac_secret(state_dir)
        return cls(config=config, hmac_secret=secret, attach_token_ttl=attach_token_ttl)

    # ---- Login ------------------------------------------------------------

    def login(self, username: str, password: str) -> str:
        """Verify credentials and return a signed session JWT.

        Raises:
            AuthError: credentials invalid or local auth disabled.
        """
        import bcrypt

        if not self._config.enabled:
            raise AuthError("local auth is disabled")

        user = self._find_user(username)
        if user is None:
            raise AuthError("invalid credentials")

        try:
            ok = bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8"))
        except Exception as exc:
            _log.warning("bcrypt check error for %s: %s", username, exc)
            raise AuthError("invalid credentials") from exc

        if not ok:
            raise AuthError("invalid credentials")

        now = int(datetime.now(UTC).timestamp())
        payload = {
            "sub": user.username,
            "role": user.role,
            "iat": now,
            "exp": now + self._config.session_ttl,
            "type": "session",
        }
        token = jwt.encode(payload, self._secret, algorithm=_JWT_ALG)
        _log.info("session JWT issued for %s (role=%s)", user.username, user.role)
        return token

    # ---- Attach token -----------------------------------------------------

    def attach_token(
        self,
        session_jwt: str,
        available_session_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Verify a session JWT and issue a scoped attach JWT.

        Args:
            session_jwt: host-issued session token from /api/auth/login.
            available_session_ids: active session IDs from the registry; used to
                scope the ``sessions`` claim. Pass None to allow all.

        Returns:
            dict with keys: token (str), expires_at (ISO-8601), mode (str).

        Raises:
            AuthError: invalid or expired session JWT.
        """
        claims = self._verify_session_jwt(session_jwt)
        role = claims.get("role", "observer")
        username = claims.get("sub", "")
        mode = _ROLE_MODE.get(role, "read")

        # Scope: for CE Phase 3, all active sessions are accessible
        sessions = available_session_ids if available_session_ids is not None else ["*"]

        now = int(datetime.now(UTC).timestamp())
        exp = now + self._attach_ttl
        attach_payload = {
            "sub": username,
            "role": role,
            "sessions": sessions,
            "mode": mode,
            "iat": now,
            "exp": exp,
            "type": "attach",
        }
        token = jwt.encode(attach_payload, self._secret, algorithm=_JWT_ALG)
        expires_at = datetime.fromtimestamp(exp, tz=UTC).isoformat()
        return {"token": token, "expires_at": expires_at, "mode": mode}

    # ---- WS token verification --------------------------------------------

    def verify_attach_token(self, token: str) -> dict[str, Any]:
        """Verify an attach JWT for WS upgrade. Returns claims on success.

        Raises:
            AuthError: signature invalid, expired, or wrong type.
        """
        try:
            claims = jwt.decode(token, self._secret, algorithms=[_JWT_ALG])
        except jwt.ExpiredSignatureError:
            raise AuthError("attach token expired") from None
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid attach token: {exc}") from exc

        if claims.get("type") != "attach":
            raise AuthError("token is not an attach token")
        return claims

    def verify_session_token(self, token: str) -> dict[str, Any]:
        """Verify a session JWT issued by ``/api/auth/login``."""
        return self._verify_session_jwt(token)

    def verify_user_token(self, token: str) -> dict[str, Any]:
        """Verify a JWT and return claims; accept either ``session`` or ``attach`` type.

        The web client exchanges its session JWT for an attach JWT at
        ``/api/terminal/attach-token`` and only retains the latter, so any
        per-user (non-session-scoped) endpoint that wants to identify the caller
        must accept either token type. Both carry the same ``sub`` claim.
        """
        try:
            claims = jwt.decode(token, self._secret, algorithms=[_JWT_ALG])
        except jwt.ExpiredSignatureError:
            raise AuthError("token expired") from None
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid token: {exc}") from exc

        if claims.get("type") not in ("session", "attach"):
            raise AuthError(f"unexpected token type: {claims.get('type')!r}")
        return claims

    # ---- Internal ---------------------------------------------------------

    def _find_user(self, username: str) -> UserRecord | None:
        for u in self._config.users:
            if u.username == username:
                return u
        return None

    def _verify_session_jwt(self, token: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(token, self._secret, algorithms=[_JWT_ALG])
        except jwt.ExpiredSignatureError:
            raise AuthError("session token expired") from None
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"invalid session token: {exc}") from exc

        if claims.get("type") != "session":
            raise AuthError("token is not a session token")
        return claims

    @property
    def enabled(self) -> bool:
        return self._config.enabled


class AuthError(ValueError):
    """Raised for invalid credentials or token verification failures."""


#: The spec's vocabulary name for the shared web-auth service; the same object
#: hosts mount. Kept as an alias so ``CeAuthManager`` importers stay unchanged.
AuthService = CeAuthManager

__all__ = [
    "AuthError",
    "AuthService",
    "CeAuthManager",
    "LocalAuthConfig",
    "UserRecord",
    "parse_local_auth_config",
]
