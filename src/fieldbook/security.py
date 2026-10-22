"""Tokens and permissions.

Two kinds of link, both stored only as SHA-256 hashes:
- session: the owner's or a manager's login link (sets a cookie for the office app);
- field: the link a person opens on their phone to talk to their Assistant.

Only people with a manager role (owner, pm) can approve. The Assistant never approves:
approval endpoints require a human session, and the ledger records which human acted.
"""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.db import utcnow
from fieldbook.models import MANAGER_ROLES, Person, Token

SESSION_COOKIE = "fb_session"


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_token(db: Session, person: Person, kind: str) -> str:
    raw = secrets.token_urlsafe(24)
    db.add(Token(person_id=person.id, token_hash=token_hash(raw), kind=kind))
    return raw


def person_for_token(db: Session, raw: str | None, kinds: tuple[str, ...]) -> Person | None:
    if not raw:
        return None
    tok = db.scalar(select(Token).where(Token.token_hash == token_hash(raw), Token.revoked_at.is_(None)))
    if tok is None or tok.kind not in kinds:
        return None
    person = db.get(Person, tok.person_id)
    if person is None or person.status == "removed":
        return None
    return person


def revoke_all(db: Session, person: Person) -> None:
    for tok in db.scalars(select(Token).where(Token.person_id == person.id, Token.revoked_at.is_(None))):
        tok.revoked_at = utcnow()


def is_manager(person: Person) -> bool:
    return person.role in MANAGER_ROLES
