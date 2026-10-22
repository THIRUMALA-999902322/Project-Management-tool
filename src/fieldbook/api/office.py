"""Office API: what the owner and project managers use."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook import ledger, services
from fieldbook.api import views
from fieldbook.api.deps import get_db, get_settings, office_user
from fieldbook.assistant import ask as ask_mod
from fieldbook.config import Settings
from fieldbook.models import Blob, Document, Input, Job, LedgerEntry, Org, Person, PlanItem, Project, Report
from fieldbook.progress import compute, project_pct

router = APIRouter(prefix="/api", tags=["office"])


def _org(db: Session, me: Person) -> Org:
    return db.get(Org, me.org_id)


def _get(db: Session, model, id_: int, org_id: int, what: str):
    obj = db.get(model, id_)
    org = getattr(obj, "org_id", None)
    if org is None and obj is not None:
        if isinstance(obj, Job):
            org = obj.project.org_id
        elif isinstance(obj, Report):
            org = obj.person.org_id
    if obj is None or org != org_id:
        raise HTTPException(404, f"{what} not found")
    return obj


# ---------------------------------------------------------------- snapshot


@router.get("/office")
def office(
    db: Session = Depends(get_db), me: Person = Depends(office_user), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """Everything the office scene needs in one call. Live changes then arrive on /api/events/stream."""
    org = _org(db, me)
    people = list(
        db.scalars(select(Person).where(Person.org_id == org.id, Person.status != "removed").order_by(Person.id))
    )
    recent: dict[int, list[Input]] = {}
    for inp in db.scalars(
        select(Input).where(Input.person_id.in_([p.id for p in people])).order_by(Input.id.desc()).limit(400)
    ):
        bucket = recent.setdefault(inp.person_id, [])
        if len(bucket) < 4:
            bucket.append(inp)
    projects = list(db.scalars(select(Project).where(Project.org_id == org.id).order_by(Project.id)))
    jobs = list(db.scalars(select(Job).where(Job.project_id.in_([p.id for p in projects])).order_by(Job.id)))
    prog = compute(db, jobs)
    reports = list(
        db.scalars(
            select(Report)
            .join(Job)
            .where(
                Job.project_id.in_([p.id for p in projects]),
                Report.status.in_(("submitted", "changes_requested", "approved")),
            )
            .order_by(Report.submitted_at)
        )
    )
    entries = {
        e.report_id: e
        for e in db.scalars(select(LedgerEntry).where(LedgerEntry.report_id.in_([r.id for r in reports])))
    }
    today_cut = max((r.decided_at for r in reports if r.decided_at), default=None)
    inbox = [
        views.report_view(r, entries.get(r.id))
        for r in reports
        if r.status != "approved" or (r.decided_at and today_cut and (today_cut - r.decided_at).total_seconds() < 86400)
    ]
    log = views.event_log(db, org.id)
    return {
        "org": {"id": org.id, "name": org.name},
        "me": {"id": me.id, "name": me.name, "role": me.role},
        "assistant": "claude" if settings.use_claude else "rules",
        "people": [views.person_view(p, recent.get(p.id, [])) for p in people],
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "short": p.short,
                "client": p.client,
                "color": p.color,
                "plan_status": p.plan_status,
                "work_types": p.work_types,
                "plan_notes": p.plan_notes,
                "team": [m.person_id for m in p.members],
                "pct": project_pct([prog[j.id] for j in jobs if j.project_id == p.id]),
            }
            for p in projects
        ],
        "jobs": [views.job_view(j, prog[j.id]) for j in jobs],
        "inbox": inbox,
        "log": log,
        "last_event_id": log[0]["id"] if log else 0,
        "suggested_questions": ask_mod.SUGGESTED,
    }


# ---------------------------------------------------------------- people


class PersonIn(BaseModel):
    name: str
    role: str
    contact: str | None = None
    company: str | None = None
    device: str = "phone"
    job_ids: list[int] = Field(default_factory=list)


class PersonPatch(BaseModel):
    name: str | None = None
    contact: str | None = None
    company: str | None = None
    role: str | None = None


@router.get("/people/{person_id}")
def get_person(person_id: int, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    p = _get(db, Person, person_id, me.org_id, "Person")
    inputs = list(db.scalars(select(Input).where(Input.person_id == p.id).order_by(Input.id.desc()).limit(12)))
    return views.person_view(p, inputs)


@router.post("/people", status_code=201)
def hire(
    body: PersonIn,
    db: Session = Depends(get_db),
    me: Person = Depends(office_user),
    settings: Settings = Depends(get_settings),
):
    person, raw = services.create_person(
        db,
        _org(db, me),
        name=body.name,
        role=body.role,
        actor=me,
        contact=body.contact,
        company=body.company,
        device=body.device,
    )
    for jid in body.job_ids:
        services.assign_job(db, _get(db, Job, jid, me.org_id, "Job"), person, me)
    return {"person": views.person_view(person), "field_link": f"{settings.public_url}/f/{raw}"}


@router.patch("/people/{person_id}")
def update_person(person_id: int, body: PersonPatch, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    p = _get(db, Person, person_id, me.org_id, "Person")
    services.update_person(db, p, me, name=body.name, contact=body.contact, role=body.role, company=body.company)
    return views.person_view(p)


@router.delete("/people/{person_id}")
def remove_person(person_id: int, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    p = _get(db, Person, person_id, me.org_id, "Person")
    jobs = services.remove_person(db, p, me)
    return {"removed": p.id, "jobs_needing_someone": [j.id for j in jobs]}


@router.post("/people/{person_id}/link")
def new_link(
    person_id: int,
    db: Session = Depends(get_db),
    me: Person = Depends(office_user),
    settings: Settings = Depends(get_settings),
):
    p = _get(db, Person, person_id, me.org_id, "Person")
    return {"field_link": f"{settings.public_url}/f/{services.new_field_link(db, p)}"}


# ---------------------------------------------------------------- jobs


class JobIn(BaseModel):
    project_id: int
    name: str
    method: str = "qty"
    total: float | None = None
    unit: str = "LF"
    assignee_id: int | None = None


class JobPatch(BaseModel):
    assignee_id: int | None


@router.post("/jobs", status_code=201)
def create_job(body: JobIn, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    project = _get(db, Project, body.project_id, me.org_id, "Project")
    if body.assignee_id:
        _get(db, Person, body.assignee_id, me.org_id, "Person")
    job = services.create_job(
        db,
        project,
        me,
        name=body.name,
        method=body.method,
        total=body.total,
        unit=body.unit,
        assignee_id=body.assignee_id,
    )
    return views.job_view(job, compute(db, [job])[job.id])


@router.patch("/jobs/{job_id}")
def assign(job_id: int, body: JobPatch, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    job = _get(db, Job, job_id, me.org_id, "Job")
    person = _get(db, Person, body.assignee_id, me.org_id, "Person") if body.assignee_id else None
    services.assign_job(db, job, person, me)
    return views.job_view(job, compute(db, [job])[job.id])


# ---------------------------------------------------------------- projects and plans


class ProjectIn(BaseModel):
    name: str
    client: str | None = None
    work_types: list[str] = Field(default_factory=list)
    team: list[int] = Field(default_factory=list)


class PlanApproval(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    for pid in body.team:
        _get(db, Person, pid, me.org_id, "Person")
    p = services.create_project(
        db, _org(db, me), me, name=body.name, client=body.client, work_types=body.work_types, team_ids=body.team
    )
    return {"id": p.id, "name": p.name, "short": p.short, "color": p.color}


@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_documents(
    project_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    me: Person = Depends(office_user),
    settings: Settings = Depends(get_settings),
):
    project = _get(db, Project, project_id, me.org_id, "Project")
    uploads = [
        services.Upload(f.filename or "file", f.content_type or "application/octet-stream", await f.read())
        for f in files
    ]
    docs = services.add_documents(db, settings, project, me, uploads)
    return {
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "pages": d.blob.pages,
                "status": d.status,
                "duplicate_of": d.duplicate_of,
            }
            for d in docs
        ]
    }


def _propose_in_background(app, project_id: int) -> None:
    db = app.state.sessions()
    try:
        services.propose_plan(db, app.state.settings, db.get(Project, project_id))
    finally:
        db.close()


@router.post("/projects/{project_id}/plan", status_code=202)
def propose(
    project_id: int,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    me: Person = Depends(office_user),
):
    project = _get(db, Project, project_id, me.org_id, "Project")
    if project.plan_status == "approved":
        raise HTTPException(400, "This plan is already approved. Add jobs one at a time instead.")
    background.add_task(_propose_in_background, request.app, project.id)
    return {"status": "reading"}


@router.get("/projects/{project_id}/plan")
def get_plan(project_id: int, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    project = _get(db, Project, project_id, me.org_id, "Project")
    items = list(db.scalars(select(PlanItem).where(PlanItem.project_id == project.id).order_by(PlanItem.position)))
    team = [db.get(Person, m.person_id) for m in project.members]
    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "short": project.short,
            "plan_status": project.plan_status,
            "notes": project.plan_notes,
        },
        "team": [
            {"id": p.id, "name": p.name, "role_label": services.ROLE_LABEL[p.role]}
            for p in team
            if p and p.status != "removed"
        ],
        "items": [
            {
                "id": i.id,
                "name": i.name,
                "tag": i.tag,
                "method": i.method,
                "quantity": i.quantity,
                "unit": i.unit,
                "source": i.source,
                "found": i.found_in_documents,
                "person_id": i.suggested_person_id,
            }
            for i in items
        ],
    }


@router.post("/projects/{project_id}/plan/approve")
def approve_plan(project_id: int, body: PlanApproval, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    project = _get(db, Project, project_id, me.org_id, "Project")
    jobs = services.approve_plan(db, project, me, body.items)
    entry = db.scalar(
        select(LedgerEntry)
        .where(LedgerEntry.project_id == project.id, LedgerEntry.kind == "plan_approved")
        .order_by(LedgerEntry.seq.desc())
    )
    return {"jobs": [j.id for j in jobs], "fingerprint": ledger.fingerprint(entry.hash)}


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    project = _get(db, Project, project_id, me.org_id, "Project")
    jobs = list(db.scalars(select(Job).where(Job.project_id == project.id).order_by(Job.id)))
    prog = compute(db, jobs)
    return {
        "id": project.id,
        "name": project.name,
        "short": project.short,
        "client": project.client,
        "color": project.color,
        "plan_status": project.plan_status,
        "pct": project_pct(list(prog.values())),
        "jobs": [views.job_view(j, prog[j.id]) for j in jobs],
        "team": [m.person_id for m in project.members],
        "history": views.project_history(db, project),
    }


# ---------------------------------------------------------------- in-tray decisions


class ChangesIn(BaseModel):
    items: list[str] = Field(default_factory=list)
    note: str | None = None


class QuestionIn(BaseModel):
    text: str


class BatchIn(BaseModel):
    ids: list[int]


def _report(db: Session, report_id: int, me: Person) -> Report:
    return _get(db, Report, report_id, me.org_id, "Report")


@router.post("/reports/{report_id}/approve")
def approve(report_id: int, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    entry = services.approve_report(db, _report(db, report_id, me), me)
    return {"report_id": report_id, "fingerprint": ledger.fingerprint(entry.hash), "seq": entry.seq}


@router.post("/reports/approve")
def approve_batch(body: BatchIn, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    out = []
    for rid in body.ids:
        entry = services.approve_report(db, _report(db, rid, me), me)
        out.append({"report_id": rid, "fingerprint": ledger.fingerprint(entry.hash)})
    return {"approved": out}


@router.post("/reports/{report_id}/changes")
def changes(report_id: int, body: ChangesIn, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    r = services.request_changes(db, _report(db, report_id, me), me, items=body.items, note=body.note)
    return views.report_view(r)


@router.post("/reports/{report_id}/questions")
def question(report_id: int, body: QuestionIn, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    r = services.ask_about_report(db, _report(db, report_id, me), me, body.text)
    return views.report_view(r)


# ---------------------------------------------------------------- ledger


class AmendIn(BaseModel):
    text: str
    reason: str
    delta: float | None = None


@router.post("/ledger/{entry_id}/amend", status_code=201)
def amend(entry_id: int, body: AmendIn, db: Session = Depends(get_db), me: Person = Depends(office_user)):
    entry = _get(db, LedgerEntry, entry_id, me.org_id, "Entry")
    new = services.amend_entry(db, entry, me, text=body.text, delta=body.delta, reason=body.reason)
    return {"id": new.id, "fingerprint": ledger.fingerprint(new.hash)}


@router.get("/ledger/verify")
def verify(db: Session = Depends(get_db), me: Person = Depends(office_user)):
    v = ledger.verify(db)
    return {"ok": v.ok, "entries": v.entries, "broken_at_seq": v.broken_at_seq, "reason": v.reason}


# ---------------------------------------------------------------- ask and files


class AskIn(BaseModel):
    question: str


@router.post("/ask")
def ask(
    body: AskIn,
    db: Session = Depends(get_db),
    me: Person = Depends(office_user),
    settings: Settings = Depends(get_settings),
):
    if not body.question.strip():
        raise HTTPException(400, "Ask a question.")
    result = ask_mod.answer(db, settings, me.org_id, body.question.strip())
    db.commit()  # keeps the usage log row
    return result


@router.get("/files/{blob_id}")
def office_file(
    blob_id: int,
    db: Session = Depends(get_db),
    me: Person = Depends(office_user),
    settings: Settings = Depends(get_settings),
):
    blob = db.get(Blob, blob_id)
    owned = blob is not None and (
        db.scalar(
            select(Input.id)
            .join(Person, Input.person_id == Person.id)
            .where(Input.blob_id == blob_id, Person.org_id == me.org_id)
            .limit(1)
        )
        or db.scalar(
            select(Document.id).join(Project).where(Document.blob_id == blob_id, Project.org_id == me.org_id).limit(1)
        )
    )
    if not owned:
        raise HTTPException(404, "File not found")
    return FileResponse(settings.blob_dir / blob.path, media_type=blob.mime)
