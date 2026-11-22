"""Role-based dependencies. Permissions are always checked server-side (spec section 7 Security)."""
import uuid
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.local import COOKIE, decode_token
from app.db import get_db
from app.models import Group, Person, Project, User

RATE_ROLES = {"admin", "sl", "gl", "pl"}


@dataclass
class CurrentUser:
    id: uuid.UUID
    email: str
    name: str
    roles: set[str]
    gl_group_ids: set[uuid.UUID] = field(default_factory=set)
    sl_group_ids: set[uuid.UUID] = field(default_factory=set)
    pl_project_ids: set[uuid.UUID] = field(default_factory=set)

    def has(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def can_see_rates(self) -> bool:
        return bool(self.roles & RATE_ROLES)

    def is_gl_of(self, person: Person | None) -> bool:
        return person is not None and person.group_id in self.gl_group_ids

    def is_pl_of(self, project: Project | None) -> bool:
        return project is not None and project.id in self.pl_project_ids

    def can_edit_allocation(self, person: Person | None, project: Project | None) -> bool:
        return self.is_admin or self.is_gl_of(person) or self.is_pl_of(project)

    def can_see_detail(self, person: Person | None, project: Project | None, visibility_mode: str) -> bool:
        """Hours/rates per project: the project's PL, the person's GL, SLs and Admins (scoped mode)."""
        if self.has("admin", "sl"):
            return True
        if self.is_gl_of(person) or self.is_pl_of(project):
            return True
        return visibility_mode == "open" and self.has("gl", "pl")

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "email": self.email,
            "name": self.name,
            "roles": sorted(self.roles),
            "gl_group_ids": [str(g) for g in self.gl_group_ids],
            "sl_group_ids": [str(g) for g in self.sl_group_ids],
            "pl_project_ids": [str(p) for p in self.pl_project_ids],
            "can_see_rates": self.can_see_rates,
        }


def load_current_user(db: Session, user: User) -> CurrentUser:
    roles = {r.role for r in user.roles}
    gl_groups = set(db.scalars(select(Group.id).where(Group.gl_user_id == user.id)))
    gl_groups |= {r.scope_group_id for r in user.roles if r.role == "gl" and r.scope_group_id}
    sl_groups = set(db.scalars(select(Group.id).where(Group.sl_user_id == user.id)))
    pl_projects = set(db.scalars(select(Project.id).where(Project.pl_user_id == user.id, Project.is_deleted.is_(False))))
    if gl_groups:
        roles.add("gl")
    if pl_projects:
        roles.add("pl")
    return CurrentUser(user.id, user.email, user.name, roles, gl_groups, sl_groups, pl_projects)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = request.cookies.get(COOKIE)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    uid = decode_token(token) if token else None
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    user = db.get(User, uuid.UUID(uid))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return load_current_user(db, user)


def require_roles(*roles: str):
    def dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires one of: {', '.join(roles)}")
        return user

    return dep


def forbid(msg: str = "Not allowed") -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, msg)
