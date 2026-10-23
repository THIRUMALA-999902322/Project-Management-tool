"""Ask: the owner's Assistant answers questions across all jobs, with sources.

A local model gets a compact snapshot of the current state (computed by code) and answers only
from it; cited sources that are not in the snapshot are dropped. Without a model, the common
questions are answered directly from the same data.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.assistant import llm
from fieldbook.config import Settings
from fieldbook.models import Event, Job, Person, Project, Report
from fieldbook.progress import compute, fmt_qty, project_pct

SYSTEM = """You are the owner's Assistant at a civil engineering firm. Answer the owner's question using only the JSON snapshot of their projects, jobs, people, reports and recent activity. Be short and specific: two to four sentences, plain construction language, numbers exactly as given. Cite your sources by copying the `label` of the reports, jobs or evidence you used. If the snapshot does not contain the answer, say what is missing instead of guessing."""

SUGGESTED = [
    "What's holding up Line A?",
    "Which reports need a decision?",
    "How far along is each project?",
    "Who hasn't sent anything today?",
]


class Source(BaseModel):
    label: str = Field(description="Label copied from the snapshot")


class AnswerOut(BaseModel):
    answer: str
    sources: list[Source]


def snapshot(db: Session, org_id: int) -> dict[str, Any]:
    people = {p.id: p for p in db.scalars(select(Person).where(Person.org_id == org_id, Person.status != "removed"))}
    projects = list(db.scalars(select(Project).where(Project.org_id == org_id)))
    jobs = list(db.scalars(select(Job).where(Job.project_id.in_([p.id for p in projects]))))
    prog = compute(db, jobs)
    reports = list(db.scalars(select(Report).where(Report.status.in_(("submitted", "changes_requested")))))
    events = list(
        db.scalars(select(Event).where(Event.org_id == org_id, Event.text != "").order_by(Event.id.desc()).limit(25))
    )
    return {
        "projects": [
            {
                "label": f"Project · {p.short}",
                "name": p.name,
                "plan": p.plan_status,
                "approved_pct": project_pct([prog[j.id] for j in jobs if j.project_id == p.id])[0],
                "pct_when_approved": project_pct([prog[j.id] for j in jobs if j.project_id == p.id])[1],
            }
            for p in projects
        ],
        "jobs": [
            {
                "label": f"Job · {j.tag}",
                "name": j.name,
                "project": next((p.short for p in projects if p.id == j.project_id), ""),
                "who": people[j.assignee_id].name if j.assignee_id in people else None,
                "progress": prog[j.id].qty_label,
                "approved_pct": prog[j.id].approved_pct,
                "pct_when_approved": prog[j.id].pending_pct,
                "status": prog[j.id].status,
            }
            for j in jobs
        ],
        "open_reports": [
            {
                "label": f"Report · {people[r.person_id].name if r.person_id in people else 'former member'} · {r.job.tag}",
                "status": r.status,
                "change": fmt_qty(r.delta, r.job.unit, sign=True),
                "stations": f"{r.sta_from} to {r.sta_to}" if r.sta_from else None,
                "verdict": r.review.get("verdict"),
                "missing": r.missing,
                "evidence": [f"{i.kind} · {i.filename or (i.text or '')[:60]}" for i in r.inputs],
            }
            for r in reports
        ],
        "people": [
            {
                "name": p.name,
                "role": p.role,
                "assistant_status": p.bubble_text,
                "link": p.link,
                "queued": p.queued,
                "inputs_today": len([e for e in events if e.person_id == p.id and e.type == "input.received"]),
            }
            for p in people.values()
        ],
        "recent_activity": [e.text for e in events],
    }


def _rules_answer(snap: dict[str, Any], question: str) -> AnswerOut:
    q = question.lower()
    if "decision" in q or "approv" in q or "tray" in q:
        rs = [r for r in snap["open_reports"] if r["status"] == "submitted"]
        if not rs:
            return AnswerOut(answer="Nothing is waiting. The in-tray is clear.", sources=[])
        lines = "; ".join(f"{r['label'].split(' · ', 1)[1]} {r['change']}: {r['verdict']}" for r in rs)
        return AnswerOut(
            answer=f"{len(rs)} in the in-tray. {lines}", sources=[Source(label=r["label"]) for r in rs[:4]]
        )
    if "far" in q or "progress" in q or "along" in q:
        parts = [
            f"{p['name']} is {p['approved_pct']}% approved ({p['pct_when_approved']}% when the in-tray clears)"
            for p in snap["projects"]
        ]
        return AnswerOut(answer=". ".join(parts) + ".", sources=[Source(label=p["label"]) for p in snap["projects"]])
    if "who" in q and ("anything" in q or "quiet" in q or "sent" in q):
        quiet = [
            p for p in snap["people"] if p["role"] != "owner" and (p["inputs_today"] == 0 or p["link"] == "offline")
        ]
        if not quiet:
            return AnswerOut(answer="Everyone has sent something today.", sources=[])
        names = ", ".join(
            p["name"] + (f" (offline, {p['queued']} items queued)" if p["link"] == "offline" else "") for p in quiet
        )
        return AnswerOut(answer=f"Nothing new today from {names}.", sources=[])
    for j in snap["jobs"]:
        if j["name"].lower().split(" · ")[0] in q or j["label"].split(" · ")[1].lower() in q:
            rs = [r for r in snap["open_reports"] if r["label"].endswith(j["label"].split(" · ")[1])]
            extra = " ".join(f"{r['change']} is in the in-tray ({r['verdict']})" for r in rs)
            return AnswerOut(
                answer=f"{j['name']}: {j['progress']}, {j['approved_pct']}% approved. Status: {j['status']}. {extra}".strip(),
                sources=[Source(label=j["label"])] + [Source(label=r["label"]) for r in rs],
            )
    return AnswerOut(
        answer="I can answer questions about jobs, reports, people and progress. Try one of the suggested questions.",
        sources=[],
    )


def _labels(snap: dict[str, Any]) -> set[str]:
    return {row["label"] for key in ("projects", "jobs", "open_reports") for row in snap[key]}


def answer(db: Session, settings: Settings, org_id: int, question: str) -> dict[str, Any]:
    snap = snapshot(db, org_id)
    out = None
    if settings.use_llm:
        # Most important first, so clipping only ever drops the oldest activity.
        data, _ = llm.clip(json.dumps(snap, ensure_ascii=False, indent=0), settings.llm_max_input_chars)
        out = llm.structured(
            db,
            settings,
            purpose="ask",
            system=SYSTEM,
            content=[llm.text_part(f"Snapshot:\n{data}\n\nQuestion: {question}")],
            output=AnswerOut,
        )
        if out is not None:
            known = _labels(snap)
            out.sources = [s for s in out.sources if s.label in known]
    by = "llm" if out is not None else "rules"
    out = out or _rules_answer(snap, question)
    return {"answer": out.answer, "sources": [s.label for s in out.sources], "answered_by": by}
