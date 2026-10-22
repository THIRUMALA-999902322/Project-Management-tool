"""Serializers: database rows -> the JSON shapes the office and field apps render."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.db import iso
from fieldbook.ledger import fingerprint
from fieldbook.models import Event, Input, Job, LedgerEntry, Person, Project, Report
from fieldbook.progress import JobProgress, fmt_qty
from fieldbook.services import ROLE_LABEL, ROOM_OF


def chip(inp: Input, n: int | None = None) -> dict[str, Any]:
    if inp.kind == "photo":
        label = f"Photo {n}" if n else "Photo"
        if inp.text:
            label += f" · {inp.text[:24]}"
    elif inp.kind == "voice":
        label = "Voice note"
    elif inp.kind == "doc":
        label = inp.filename or "File"
    else:
        label = f"“{(inp.text or '')[:30]}”"
    return {"label": label, "kind": inp.kind, "blob_id": inp.blob_id, "input_id": inp.id}


def input_view(inp: Input) -> dict[str, Any]:
    text = inp.text
    if inp.kind == "photo":
        text = "Photo" + (f" · {inp.text}" if inp.text else "")
    elif inp.kind == "doc":
        text = inp.filename
    elif inp.kind == "voice":
        text = "Voice note"
    return {
        "id": inp.id,
        "kind": inp.kind,
        "text": text,
        "quote": inp.text if inp.kind == "voice" else None,
        "at": iso(inp.created_at),
        "blob_id": inp.blob_id,
        "gps": inp.lat is not None,
        "report_id": inp.report_id,
    }


def person_view(p: Person, inputs: list[Input] | None = None) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "role": p.role,
        "role_label": ROLE_LABEL[p.role],
        "room": ROOM_OF[p.role],
        "company": p.company,
        "contact": p.contact,
        "device": p.device,
        "where": p.location,
        "status": p.status,
        "link": p.link,
        "queued": p.queued,
        "look": p.look,
        "bubble": {"kind": p.bubble_kind, "text": p.bubble_text, "icon": p.bubble_icon},
        "hired": p.hired_at is not None,
        "inputs": [input_view(i) for i in (inputs or [])],
    }


def job_view(j: Job, prog: JobProgress) -> dict[str, Any]:
    return {
        "id": j.id,
        "project_id": j.project_id,
        "tag": j.tag,
        "name": j.name,
        "method": j.method,
        "unit": j.unit,
        "word": j.word,
        "total": j.total,
        "done": prog.done,
        "pend": prog.pend,
        "approved_pct": prog.approved_pct,
        "pending_pct": prog.pending_pct,
        "status": [prog.tone, prog.status],
        "qty_label": prog.qty_label,
        "assignee_id": j.assignee_id,
        "checklist": j.checklist,
    }


def report_view(r: Report, entry: LedgerEntry | None = None) -> dict[str, Any]:
    job = r.job
    photos = 0
    chips = []
    for inp in r.inputs:
        if inp.kind == "photo":
            photos += 1
        if inp.kind != "text":
            chips.append(chip(inp, photos if inp.kind == "photo" else None))
    rv = r.review or {}
    change = fmt_qty(r.delta, job.unit, sign=True) if r.delta else (r.summary or "No quantity")
    return {
        "id": r.id,
        "person_id": r.person_id,
        "job_id": r.job_id,
        "state": r.status,
        "change": change,
        "delta": r.delta,
        "where": f"Sta {r.sta_from} to {r.sta_to}" if r.sta_from and r.sta_to else None,
        "summary": r.summary,
        "claims": r.claims,
        "missing": r.missing,
        "question": r.question,
        "drafted_by": r.drafted_by,
        "group": rv.get("group"),
        "verdict": rv.get("verdict"),
        "flags": rv.get("flags", []),
        "claimed": rv.get("claimed", r.claims),
        "shows": rv.get("shows", []),
        "usual": rv.get("usual"),
        "questions": rv.get("questions", []),
        "checked_by": rv.get("checked_by"),
        "note": rv.get("note"),
        "chips": chips[:6],
        "evidence_count": len(r.inputs),
        "asked": [q["text"] for q in r.manager_questions],
        "change_note": r.change_note,
        "submitted_at": iso(r.submitted_at),
        "updated_at": iso(r.updated_at),
        "lock": {"at": iso(entry.created_at), "fingerprint": fingerprint(entry.hash), "by": entry.actor_name}
        if entry
        else None,
    }


def entry_view(e: LedgerEntry, amendments: list[LedgerEntry]) -> dict[str, Any]:
    latest = amendments[-1] if amendments else None
    return {
        "id": e.id,
        "seq": e.seq,
        "kind": e.kind,
        "text": e.text,
        "delta": e.delta,
        "by": e.actor_name,
        "at": iso(e.created_at),
        "fingerprint": fingerprint(e.hash),
        "hash": e.hash,
        "amendment": {
            "text": latest.text,
            "by": latest.actor_name,
            "at": iso(latest.created_at),
            "reason": latest.payload.get("reason"),
            "fingerprint": fingerprint(latest.hash),
        }
        if latest
        else None,
    }


def project_history(db: Session, project: Project) -> list[dict[str, Any]]:
    entries = list(
        db.scalars(select(LedgerEntry).where(LedgerEntry.project_id == project.id).order_by(LedgerEntry.seq.desc()))
    )
    amends: dict[int, list[LedgerEntry]] = {}
    for e in sorted(entries, key=lambda x: x.seq):
        if e.kind == "amendment" and e.amends_id:
            amends.setdefault(e.amends_id, []).append(e)
    return [entry_view(e, amends.get(e.id, [])) for e in entries if e.kind != "amendment"]


def event_log(db: Session, org_id: int, limit: int = 40) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Event).where(Event.org_id == org_id, Event.text != "").order_by(Event.id.desc()).limit(limit)
    )
    out = []
    for e in rows:
        c = None
        if e.data.get("blob_id"):
            c = {
                "label": e.data.get("label") or "File",
                "kind": e.data.get("kind", "doc"),
                "blob_id": e.data["blob_id"],
            }
        out.append(
            {"id": e.id, "type": e.type, "at": iso(e.created_at), "text": e.text, "person_id": e.person_id, "chip": c}
        )
    return out
