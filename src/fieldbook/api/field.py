"""Field app API: what a person uses to talk to their Assistant (phone or laptop).

A person only ever sees their own jobs, inputs and reports. Contractors get the same view,
which is what "own scope only" means in practice.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook import services
from fieldbook.api import views
from fieldbook.api.deps import field_user, get_db, get_settings
from fieldbook.config import Settings
from fieldbook.models import Blob, Input, Job, Person, Report
from fieldbook.progress import compute

router = APIRouter(prefix="/api/field", tags=["field"])


def _redraft_in_background(app, report_id: int) -> None:
    db = app.state.sessions()
    try:
        report = db.get(Report, report_id)
        if report is not None and report.status in ("draft", "changes_requested"):
            services.redraft(db, app.state.settings, report)
    finally:
        db.close()


@router.get("/me")
def me(db: Session = Depends(get_db), person: Person = Depends(field_user)):
    jobs = list(db.scalars(select(Job).where(Job.assignee_id == person.id).order_by(Job.id)))
    prog = compute(db, jobs)
    reports = list(db.scalars(select(Report).where(Report.person_id == person.id).order_by(Report.id.desc()).limit(20)))
    inputs = list(db.scalars(select(Input).where(Input.person_id == person.id).order_by(Input.id.desc()).limit(60)))
    manager = services.manager_name(db, person.org_id)
    return {
        "person": views.person_view(person),
        "manager": manager,
        "jobs": [views.job_view(j, prog[j.id]) for j in jobs],
        "reports": [views.report_view(r) for r in reports],
        "inputs": [views.input_view(i) for i in reversed(inputs)],
    }


@router.post("/inputs", status_code=201)
async def send(
    request: Request,
    background: BackgroundTasks,
    kind: str = Form(...),
    text: str | None = Form(None),
    client_id: str | None = Form(None),
    job_id: int | None = Form(None),
    captured_at: str | None = Form(None),
    lat: float | None = Form(None),
    lon: float | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    person: Person = Depends(field_user),
    settings: Settings = Depends(get_settings),
):
    upload = None
    if file is not None:
        upload = services.Upload(
            file.filename or "file", file.content_type or "application/octet-stream", await file.read()
        )
    when = None
    if captured_at:
        try:
            when = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            when = None
    inp, report, created = services.add_input(
        db,
        settings,
        person,
        kind=kind,
        text=text,
        upload=upload,
        client_id=client_id,
        job_id=job_id,
        captured_at=when,
        lat=lat,
        lon=lon,
    )
    if created and report is not None:
        background.add_task(_redraft_in_background, request.app, report.id)
    return {"input": views.input_view(inp), "report_id": report.id if report else None, "duplicate": not created}


@router.post("/reports/{report_id}/submit")
def submit(
    report_id: int,
    db: Session = Depends(get_db),
    person: Person = Depends(field_user),
    settings: Settings = Depends(get_settings),
):
    report = db.get(Report, report_id)
    if report is None or report.person_id != person.id:
        raise HTTPException(404, "Report not found")
    services.submit_report(db, settings, report, person)
    return views.report_view(report)


class Heartbeat(BaseModel):
    online: bool = True
    queued: int = 0


@router.post("/heartbeat")
def heartbeat(body: Heartbeat, db: Session = Depends(get_db), person: Person = Depends(field_user)):
    services.heartbeat(db, person, online=body.online, queued=body.queued)
    return {"ok": True}


@router.get("/files/{blob_id}")
def own_file(
    blob_id: int,
    db: Session = Depends(get_db),
    person: Person = Depends(field_user),
    settings: Settings = Depends(get_settings),
):
    if not db.scalar(select(Input.id).where(Input.blob_id == blob_id, Input.person_id == person.id).limit(1)):
        raise HTTPException(404, "File not found")
    blob = db.get(Blob, blob_id)
    return FileResponse(settings.blob_dir / blob.path, media_type=blob.mime)
