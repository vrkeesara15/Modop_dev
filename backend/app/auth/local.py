"""AUTH_MODE=local: email + password with hashed passwords and a signed JWT session cookie."""
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGO = "HS256"
COOKIE = "rah_session"


def hash_password(p: str) -> str:
    return pwd.hash(p)


def verify_password(p: str, h: str | None) -> bool:
    return bool(h) and pwd.verify(p, h)


def create_token(user_id: str) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=s.TOKEN_TTL_HOURS)
    return jwt.encode({"sub": user_id, "exp": exp}, s.SECRET_KEY, algorithm=ALGO)


def decode_token(token: str) -> str | None:
    try:
        return jwt.decode(token, get_settings().SECRET_KEY, algorithms=[ALGO]).get("sub")
    except JWTError:
        return None
