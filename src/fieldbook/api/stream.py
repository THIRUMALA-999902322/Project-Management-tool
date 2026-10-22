"""Server-sent events: the office and the field app stay live without polling the whole state.

The stream reads the events table (id > last seen), so it is correct across several worker
processes and resumes from `Last-Event-ID` after a reconnect. One query per second per open
stream is fine at MVP scale; Postgres LISTEN/NOTIFY can replace the poll later.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from fieldbook.api.deps import field_user, office_user
from fieldbook.events import event_dict
from fieldbook.models import Event, Person

router = APIRouter(prefix="/api", tags=["events"])
POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 15.0


def _fetch(app, org_id: int, after: int, person_id: int | None) -> list[dict]:
    db = app.state.sessions()
    try:
        q = select(Event).where(Event.org_id == org_id, Event.id > after)
        if person_id is not None:
            q = q.where(Event.person_id == person_id)
        return [event_dict(e) for e in db.scalars(q.order_by(Event.id).limit(200))]
    finally:
        db.close()


def _latest(app, org_id: int) -> int:
    db = app.state.sessions()
    try:
        return db.scalar(select(func.max(Event.id)).where(Event.org_id == org_id)) or 0
    finally:
        db.close()


async def _stream(request: Request, org_id: int, after: int | None, person_id: int | None) -> AsyncIterator[str]:
    last = after if after is not None else await run_in_threadpool(_latest, request.app, org_id)
    quiet_since = time.monotonic()
    yield "retry: 3000\n\n"
    while not await request.is_disconnected():
        rows = await run_in_threadpool(_fetch, request.app, org_id, last, person_id)
        for row in rows:
            last = row["id"]
            yield f"id: {row['id']}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
        if rows:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since > KEEPALIVE_SECONDS:
            quiet_since = time.monotonic()
            yield ": keepalive\n\n"
        if request.app.state.stream_once:  # tests read one batch and stop
            break
        await asyncio.sleep(POLL_SECONDS)


def _resume_point(request: Request, after: int | None) -> int | None:
    header = request.headers.get("last-event-id")
    if header and header.isdigit():
        return int(header)
    return after


HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/events/stream")
async def office_stream(request: Request, after: int | None = None, me: Person = Depends(office_user)):
    return StreamingResponse(
        _stream(request, me.org_id, _resume_point(request, after), None),
        media_type="text/event-stream",
        headers=HEADERS,
    )


@router.get("/field/stream")
async def field_stream(request: Request, after: int | None = None, me: Person = Depends(field_user)):
    return StreamingResponse(
        _stream(request, me.org_id, _resume_point(request, after), me.id),
        media_type="text/event-stream",
        headers=HEADERS,
    )
