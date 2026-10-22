"""The field book: every notable thing becomes an Event row.

The office app shows events as the day's log and turns them into animations (a card dropping
onto a desk, a folder flying to the in-tray). The SSE stream reads this table, so it works
the same with one process or several.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from fieldbook.db import iso
from fieldbook.models import Event, Person


def emit(
    db: Session,
    org_id: int,
    type: str,
    text: str = "",
    *,
    person: Person | None = None,
    actor: Person | None = None,
    **data: Any,
) -> Event:
    event = Event(
        org_id=org_id,
        type=type,
        text=text,
        person_id=person.id if person else None,
        actor_id=actor.id if actor else None,
        data=data,
    )
    db.add(event)
    return event


def set_bubble(db: Session, person: Person, kind: str, text: str, icon: str | None = None) -> Event:
    """Update what a person's Assistant is shown doing, and tell the office."""
    person.bubble_kind, person.bubble_text, person.bubble_icon = kind, text[:200], icon
    return emit(db, person.org_id, "assistant.status", person=person, kind=kind, label=text, icon=icon)


def event_dict(e: Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "type": e.type,
        "at": iso(e.created_at),
        "text": e.text,
        "person_id": e.person_id,
        "actor_id": e.actor_id,
        "data": e.data,
    }
