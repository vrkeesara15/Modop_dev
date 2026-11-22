"""ALR-3: daily digest of new alerts by email (SMTP) and Microsoft Teams incoming webhook."""
import logging
import smtplib
from collections import defaultdict
from email.message import EmailMessage

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Alert, User

log = logging.getLogger(__name__)


def build_digest(db) -> dict[User, list[Alert]]:
    by_user = defaultdict(list)
    for a, u in db.execute(select(Alert, User).join(User, User.id == Alert.user_id).where(Alert.status == "new", User.is_active.is_(True)).order_by(Alert.created_at)):
        by_user[u].append(a)
    return by_user


def send_all() -> dict:
    s = get_settings()
    sent_email = sent_teams = 0
    with SessionLocal() as db:
        digest = build_digest(db)
        if s.SMTP_HOST:
            for u, alerts in digest.items():
                msg = EmailMessage()
                msg["Subject"] = f"Resource Allocation Hub: {len(alerts)} new alert(s)"
                msg["From"], msg["To"] = s.SMTP_FROM, u.email
                msg.set_content("\n".join(f"- [{a.severity}] {a.message}" for a in alerts) + f"\n\nOpen the app: {s.APP_BASE_URL}")
                try:
                    with smtplib.SMTP(s.SMTP_HOST, s.SMTP_PORT, timeout=20) as smtp:
                        smtp.starttls()
                        if s.SMTP_USER:
                            smtp.login(s.SMTP_USER, s.SMTP_PASSWORD)
                        smtp.send_message(msg)
                    sent_email += 1
                except Exception:
                    log.exception("digest email to %s failed", u.email)
        if s.TEAMS_WEBHOOK_URL and digest:
            total = sum(len(v) for v in digest.values())
            lines = [f"**{u.name}**: {len(v)} new" for u, v in digest.items()]
            try:
                httpx.post(s.TEAMS_WEBHOOK_URL, json={"text": f"Resource Allocation Hub daily digest: {total} new alert(s)\n\n" + "\n\n".join(lines) + f"\n\n{s.APP_BASE_URL}"}, timeout=20)
                sent_teams = 1
            except Exception:
                log.exception("Teams webhook failed")
    return {"emails": sent_email, "teams": sent_teams}
