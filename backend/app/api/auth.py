from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import local
from app.auth.deps import CurrentUser, get_current_user, load_current_user
from app.config import get_settings
from app.db import get_db
from app.models import Person, User

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


def _set_cookie(resp: Response, user_id: str) -> None:
    s = get_settings()
    resp.set_cookie(local.COOKIE, local.create_token(user_id), httponly=True, samesite="lax", secure=s.COOKIE_SECURE, max_age=s.TOKEN_TTL_HOURS * 3600)


@router.get("/api/auth/config")
def auth_config(db: Session = Depends(get_db)):
    s = get_settings()
    demo = []
    if s.AUTH_MODE == "local":
        for email in ("admin@demo", "sl@demo", "gl@demo", "pl@demo", "pl2@demo"):
            u = db.scalar(select(User).where(User.email == email))
            if u:
                demo.append({"email": u.email, "name": u.name, "roles": sorted({r.role for r in u.roles})})
    return {"mode": s.AUTH_MODE, "demo_users": demo}


@router.post("/api/auth/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    if get_settings().AUTH_MODE != "local":
        raise HTTPException(400, "Password login is disabled; use SSO")
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if not user or not user.is_active or not local.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password")
    token = local.create_token(str(user.id))
    _set_cookie(response, str(user.id))
    return {"token": token, "user": _me(db, load_current_user(db, user))}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(local.COOKIE)
    return {"ok": True}


def _me(db: Session, user: CurrentUser) -> dict:
    d = user.as_dict()
    p = db.scalar(select(Person).where(func.lower(Person.email) == user.email.lower()))
    d["person_id"] = str(p.id) if p else None
    return d


@router.get("/api/me")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return _me(db, user)


# ---- Entra ID (OIDC) - production
@router.get("/api/auth/oidc/login")
def oidc_login():
    if get_settings().AUTH_MODE != "oidc":
        raise HTTPException(404, "OIDC not enabled")
    from app.auth import oidc

    url, state = oidc.authorize_url()
    resp = RedirectResponse(url)
    resp.set_cookie("oidc_state", state, httponly=True, samesite="lax", max_age=600)
    return resp


@router.get("/api/auth/oidc/callback")
def oidc_callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
    if get_settings().AUTH_MODE != "oidc":
        raise HTTPException(404, "OIDC not enabled")
    if request.cookies.get("oidc_state") != state:
        raise HTTPException(400, "state mismatch")
    from app.auth import oidc

    info = oidc.exchange_code(code)
    user = db.scalar(select(User).where(User.sso_subject == info["sub"])) or db.scalar(select(User).where(func.lower(User.email) == info["email"]))
    if user is None:
        user = User(email=info["email"], name=info["name"], sso_subject=info["sub"], updated_by="oidc")
        db.add(user)
    user.sso_subject = info["sub"]
    db.commit()
    if not user.is_active:
        raise HTTPException(403, "account disabled")
    resp = RedirectResponse("/")
    _set_cookie(resp, str(user.id))
    return resp
