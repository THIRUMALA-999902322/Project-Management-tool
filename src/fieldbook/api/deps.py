"""Request dependencies: database session, settings, and who is asking.

- Office endpoints need a manager (owner or pm) signed in with a session link (cookie or
  Bearer). With FIELDBOOK_DEV_AUTOLOGIN=1, a browser with no session is treated as the owner.
- Field endpoints need the person's field link token (Bearer, or ?token= for EventSource).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.config import Settings
from fieldbook.models import MANAGER_ROLES, Person
from fieldbook.security import SESSION_COOKIE, person_for_token


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.sessions()
    try:
        yield db
    finally:
        db.close()


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def office_user(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Person:
    raw = _bearer(request) or request.cookies.get(SESSION_COOKIE)
    person = person_for_token(db, raw, ("session",))
    if person is None and settings.dev_autologin:
        person = db.scalar(select(Person).where(Person.role == "owner").order_by(Person.id))
    if person is None:
        raise HTTPException(401, "Sign in with your login link.")
    if person.role not in MANAGER_ROLES:
        raise HTTPException(403, "The office view is for the owner and project managers.")
    return person


def field_user(request: Request, db: Session = Depends(get_db)) -> Person:
    raw = _bearer(request) or request.query_params.get("token")
    person = person_for_token(db, raw, ("field",))
    if person is None:
        raise HTTPException(401, "This link no longer works. Ask your manager for a new one.")
    return person
