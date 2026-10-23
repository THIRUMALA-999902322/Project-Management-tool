"""The field book: every notable thing becomes an Event row.

The office app shows events as the activity feed and as live updates (a new report in the
approval queue, an Assistant's status). The SSE stream reads this table, so it works the same
with one process or several.
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


def set_activity(db: Session, person: Person, kind: str, text: str) -> Event:
    """Update what a person's Assistant is doing, and tell the office."""
    person.activity_kind, person.activity_text = kind, text[:200]
    return emit(db, person.org_id, "assistant.status", person=person, kind=kind, label=text)


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
