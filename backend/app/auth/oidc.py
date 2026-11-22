"""AUTH_MODE=oidc: Microsoft Entra ID (OpenID Connect) adapter.

This is the only module that knows about the identity provider. It implements the standard
authorization-code flow against any OIDC issuer (Entra ID: https://login.microsoftonline.com/<tenant>/v2.0).
Users are matched by sso_subject, then by email; unknown users are created with no roles so an
Admin can grant them. It is wired but not exercised in the demo (AUTH_MODE=local).
"""
import secrets
from urllib.parse import urlencode

import httpx
from jose import jwt

from app.config import get_settings

_discovery_cache: dict = {}


def _discovery() -> dict:
    s = get_settings()
    if s.OIDC_ISSUER not in _discovery_cache:
        url = s.OIDC_ISSUER.rstrip("/") + "/.well-known/openid-configuration"
        _discovery_cache[s.OIDC_ISSUER] = httpx.get(url, timeout=10).json()
    return _discovery_cache[s.OIDC_ISSUER]


def authorize_url() -> tuple[str, str]:
    s = get_settings()
    state = secrets.token_urlsafe(24)
    q = urlencode(
        {
            "client_id": s.OIDC_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": s.OIDC_REDIRECT_URI or s.APP_BASE_URL.rstrip("/") + "/api/auth/oidc/callback",
            "scope": "openid email profile",
            "state": state,
        }
    )
    return f"{_discovery()['authorization_endpoint']}?{q}", state


def exchange_code(code: str) -> dict:
    """Return {'sub', 'email', 'name'} from the ID token."""
    s = get_settings()
    d = _discovery()
    tok = httpx.post(
        d["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": s.OIDC_CLIENT_ID,
            "client_secret": s.OIDC_CLIENT_SECRET,
            "redirect_uri": s.OIDC_REDIRECT_URI or s.APP_BASE_URL.rstrip("/") + "/api/auth/oidc/callback",
        },
        timeout=10,
    ).json()
    jwks = httpx.get(d["jwks_uri"], timeout=10).json()
    claims = jwt.decode(tok["id_token"], jwks, algorithms=["RS256"], audience=s.OIDC_CLIENT_ID, options={"verify_at_hash": False})
    return {
        "sub": claims["sub"],
        "email": (claims.get("email") or claims.get("preferred_username") or "").lower(),
        "name": claims.get("name") or claims.get("email") or "User",
    }
