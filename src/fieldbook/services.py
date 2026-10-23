"""Business operations. The API and the seed both go through these, so behaviour is one place.

Each function changes state, emits the events the office animates, and commits. Functions
that write to the ledger commit through `ledger.append_and_commit`, so the decision and its
ledger entry land in one transaction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fieldbook import ledger
from fieldbook.assistant import drafting, planning
from fieldbook.assistant import review as review_mod
from fieldbook.assistant.rules import find_station_range
from fieldbook.config import Settings
from fieldbook.db import utcnow
from fieldbook.events import emit, set_bubble
from fieldbook.models import (
    FIELD_ROLES,
    MANAGER_ROLES,
    ROLES,
    Document,
    Input,
    Job,
    LedgerEntry,
    Org,
    Person,
    PlanItem,
    Project,
    ProjectMember,
    Report,
)
from fieldbook.progress import fmt_qty
from fieldbook.security import issue_token, revoke_all
from fieldbook.storage import photo_meta, save_blob

ROLE_LABEL = {
    "owner": "Owner",
    "pm": "Project manager",
    "engineer": "Design engineer",
    "cad": "CAD tech",
    "qaqc": "QA/QC reviewer",
    "crew": "Crew lead",
    "inspector": "Inspector",
    "contractor": "Contractor",
}
ROOM_OF = {
    "owner": "owner",
    "pm": "pm",
    "engineer": "design",
    "cad": "design",
    "qaqc": "qaqc",
    "crew": "trailer",
    "inspector": "trailer",
    "contractor": "gate",
}
PROJECT_COLORS = ["#1F6F8B", "#9C5A2E", "#5B4B8A", "#3F7F6E", "#8A3B5C"]
SKINS = ["#F3D2B3", "#EAC39B", "#D9A47A", "#C68642", "#A86B46", "#8D5A3B", "#6B4430"]
HAIRS = ["short", "bun", "long", "curly", "buzz"]
HAIR_COLORS = ["#1E1B1A", "#3A2618", "#6B4B32", "#8B4A2B", "#C9A15B", "#2B211C"]
SHIRTS = ["#2F6F73", "#6E94C8", "#7A9E7E", "#8C3B4A", "#5B4B8A", "#3F5A7A"]
FIELD_HAT = {"crew": "#F4F4F0", "inspector": "#3B6FB6", "contractor": "#E46B1F"}


class Forbidden(Exception):
    pass


class Invalid(Exception):
    pass


def first_name(p: Person) -> str:
    return p.name if "." in p.name.split(" ")[0] else p.name.split(" ")[0]


def look_for_role(look: dict[str, Any], role: str) -> dict[str, Any]:
    """Dress the desk character for the role: hard hat and vest in the field, office shirt inside."""
    out = dict(look)
    if role in FIELD_ROLES:
        out.setdefault("office_shirt", out.get("shirt"))
        out["hat"] = FIELD_HAT[role] if out.get("hat") in (None, *FIELD_HAT.values()) else out["hat"]
        out["vest"] = "#D7E23A" if role == "contractor" else "#F07F1E"
        out["shirt"], out["prop"] = "#4B5563", "radio"
    else:
        out.pop("hat", None)
        out.pop("vest", None)
        out["shirt"] = out.get("office_shirt") or out.get("shirt") or "#3F5A7A"
        out["prop"] = {"engineer": "tube", "qaqc": "binder"}.get(role, "mug")
    return out


def generated_look(name: str, role: str) -> dict[str, Any]:
    seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    look = {
        "skin": SKINS[seed % len(SKINS)],
        "hair": HAIRS[(seed >> 3) % len(HAIRS)],
        "hair_color": HAIR_COLORS[(seed >> 6) % len(HAIR_COLORS)],
        "shirt": SHIRTS[(seed >> 9) % len(SHIRTS)],
        "glasses": (seed >> 12) % 3 == 0,
        "beard": (seed >> 14) % 5 == 0,
    }
    return look_for_role(look, role)


# ---------------------------------------------------------------- people


def manager_name(db: Session, org_id: int) -> str:
    pm = db.scalar(select(Person).where(Person.org_id == org_id, Person.role == "pm", Person.status != "removed"))
    return pm.name if pm else "the manager"


def create_person(
    db: Session,
    org: Org,
    *,
    name: str,
    role: str,
    actor: Person | None,
    contact: str | None = None,
    device: str = "phone",
    company: str | None = None,
    location: str | None = None,
    look: dict[str, Any] | None = None,
    announce: bool = True,
) -> tuple[Person, str]:
    """Add someone. Returns the person and the raw field-app token (shown once, as a link)."""
    name = name.strip()
    if not name:
        raise Invalid("A name is needed for the nameplate.")
    if role not in ROLES or role == "owner":
        raise Invalid(f"Unknown role: {role}")
    person = Person(
        org_id=org.id,
        name=name,
        role=role,
        contact=contact,
        device=device,
        company=company,
        location=location or ("On site" if role in FIELD_ROLES else ROLE_LABEL[role]),
        status="invited",
        look=look or generated_look(name, role),
        hired_at=utcnow(),
    )
    db.add(person)
    db.flush()
    raw = issue_token(db, person, "field")
    first = first_name(person)
    set_bubble(db, person, "idle", f"Hi {first}. Send me a photo", "chat")
    if announce:
        emit(
            db,
            org.id,
            "person.joined",
            f"{name} joined as {ROLE_LABEL[role]}. Their Assistant is set up.",
            person=person,
            actor=actor,
            room=ROOM_OF[role],
        )
    db.commit()
    return person, raw


def update_person(
    db: Session, person: Person, actor: Person, *, name=None, contact=None, role=None, company=None
) -> Person:
    if person.status == "removed":
        raise Invalid("This person was removed from the office.")
    if name is not None:
        if not name.strip():
            raise Invalid("A name is needed for the nameplate.")
        person.name = name.strip()
    if contact is not None:
        person.contact = contact.strip() or None
    if company is not None:
        person.company = company.strip() or None
    if role is not None and role != person.role:
        if person.role == "owner" or role == "owner" or role not in ROLES:
            raise Invalid("The owner's role can't be changed here.")
        old = person.role
        person.role = role
        person.look = look_for_role(person.look, role)
        if role in FIELD_ROLES and old not in FIELD_ROLES:
            person.location = "On site"
        elif role not in FIELD_ROLES:
            person.location = ROLE_LABEL[role]
        set_bubble(db, person, "ok", f"Now {ROLE_LABEL[role]}", "check")
        emit(
            db,
            person.org_id,
            "person.role_changed",
            f"{person.name} moved from {ROLE_LABEL[old]} to {ROLE_LABEL[role]}. Their Assistant's permissions changed with the role.",
            person=person,
            actor=actor,
            old_room=ROOM_OF[old],
            room=ROOM_OF[role],
        )
    else:
        emit(db, person.org_id, "person.updated", person=person, actor=actor)
    db.commit()
    return person


def remove_person(db: Session, person: Person, actor: Person) -> list[Job]:
    """Remove from the office. Tokens stop working; approved history stays; jobs need someone new."""
    if person.role == "owner":
        raise Invalid("The owner can't be removed.")
    jobs = list(db.scalars(select(Job).where(Job.assignee_id == person.id)))
    for j in jobs:
        j.assignee_id = None
    person.status, person.removed_at = "removed", utcnow()
    revoke_all(db, person)
    emit(
        db,
        person.org_id,
        "person.removed",
        f"{person.name} left the office. Their approved history stays in the record.",
        person=person,
        actor=actor,
        room=ROOM_OF[person.role],
        jobs=[j.id for j in jobs],
    )
    db.commit()
    return jobs


def new_field_link(db: Session, person: Person) -> str:
    raw = issue_token(db, person, "field")
    db.commit()
    return raw


def heartbeat(db: Session, person: Person, *, online: bool, queued: int) -> None:
    was_offline = person.link == "offline"
    person.link = "online" if online else "offline"
    person.queued = max(0, queued)
    if person.status == "invited":
        person.status = "active"
    if online and was_offline:
        set_bubble(db, person, "ok", "Back online", "check")
        emit(db, person.org_id, "person.online", f"{person.name} is back online.", person=person)
    db.commit()


# ---------------------------------------------------------------- projects and jobs


def create_project(
    db: Session, org: Org, actor: Person, *, name: str, client: str | None, work_types: list[str], team_ids: list[int]
) -> Project:
    if not name.strip():
        raise Invalid("Give the project a name.")
    count = db.scalar(select(func.count(Project.id)).where(Project.org_id == org.id)) or 0
    short = " ".join(name.split()[:2])
    project = Project(
        org_id=org.id,
        name=name.strip(),
        short=short,
        client=client,
        work_types=work_types,
        color=PROJECT_COLORS[count % len(PROJECT_COLORS)],
        plan_status="draft",
    )
    db.add(project)
    db.flush()
    team = set(team_ids)
    team.update(
        p.id
        for p in db.scalars(
            select(Person).where(Person.org_id == org.id, Person.role.in_(MANAGER_ROLES), Person.status != "removed")
        )
    )
    for pid in sorted(team):
        if db.get(Person, pid):
            project.members.append(ProjectMember(person_id=pid))
    emit(db, org.id, "project.created", f"{project.name} was set up.", actor=actor, project_id=project.id)
    db.commit()
    return project


@dataclass
class Upload:
    filename: str
    mime: str
    data: bytes


def add_documents(
    db: Session, settings: Settings, project: Project, actor: Person, files: list[Upload]
) -> list[Document]:
    """Store scope documents. A file whose bytes were already uploaded to this org is kept but skipped."""
    out = []
    org_doc_blobs = {
        d.blob_id: d.filename
        for d in db.scalars(
            select(Document).join(Project).where(Project.org_id == project.org_id, Document.status == "ready")
        )
    }
    for f in files:
        blob = save_blob(db, settings, f.data, f.mime)
        dup_of = org_doc_blobs.get(blob.id)
        doc = Document(
            project_id=project.id,
            blob_id=blob.id,
            filename=f.filename,
            uploaded_by_id=actor.id,
            status="duplicate" if dup_of else "ready",
            duplicate_of=dup_of,
        )
        db.add(doc)
        db.flush()
        if not dup_of:
            org_doc_blobs[blob.id] = f.filename
        out.append(doc)
    pages = sum((d.blob.pages or 0) for d in out if d.status == "ready")
    dups = sum(1 for d in out if d.status == "duplicate")
    emit(
        db,
        project.org_id,
        "documents.uploaded",
        f"{len(out) - dups} scope document{'s' if len(out) - dups != 1 else ''} added to {project.short}"
        + (f", {dups} duplicate skipped" if dups else "")
        + ".",
        actor=actor,
        project_id=project.id,
        pages=pages,
        duplicates=dups,
    )
    db.commit()
    return out


def propose_plan(db: Session, settings: Settings, project: Project) -> list[PlanItem]:
    project.plan_status = "reading"
    docs = list(db.scalars(select(Document).where(Document.project_id == project.id, Document.status == "ready")))
    pages = sum(d.blob.pages or 0 for d in docs)
    emit(db, project.org_id, "plan.reading", project_id=project.id, pages=pages, files=len(docs))
    db.commit()

    proposed, notes, by = planning.propose(db, settings, project)
    for old in db.scalars(select(PlanItem).where(PlanItem.project_id == project.id)):
        db.delete(old)
    team = [db.get(Person, m.person_id) for m in project.members]
    team = [p for p in team if p and p.status != "removed"]
    items = []
    for n, it in enumerate(proposed):
        who = next((p for r in it.roles for p in team if p.role == r), None)
        if who is None:
            # Same kind of work (field or office) first, then the PM; the owner is the last resort.
            field_work = any(r in FIELD_ROLES for r in it.roles)
            ranked = sorted(
                team, key=lambda p: ((p.role in FIELD_ROLES) != field_work, p.role == "owner", p.role != "pm")
            )
            who = ranked[0] if ranked else None
        item = PlanItem(
            project_id=project.id,
            position=n,
            name=it.name,
            tag=it.tag,
            method=it.method,
            quantity=it.quantity,
            unit=it.unit,
            source=it.source,
            found_in_documents=it.found,
            checklist=it.checklist,
            suggested_person_id=who.id if who else None,
        )
        db.add(item)
        items.append(item)
    project.plan_status = "review"
    project.plan_notes = notes
    found = sum(1 for i in proposed if i.found)
    emit(
        db,
        project.org_id,
        "plan.proposed",
        f"The Assistant read {pages} pages for {project.short} and proposed {len(items)} plan items"
        + (f"; {len(items) - found} need a quantity from you" if found < len(items) else "")
        + ".",
        project_id=project.id,
        items=len(items),
        pages=pages,
        by=by,
    )
    db.commit()
    return items


def approve_plan(db: Session, project: Project, actor: Person, edits: list[dict[str, Any]]) -> list[Job]:
    if actor.role not in MANAGER_ROLES:
        raise Forbidden("Only the owner or a project manager approves a plan.")
    if project.plan_status != "review":
        raise Invalid("This plan isn't waiting for approval.")
    items = {i.id: i for i in db.scalars(select(PlanItem).where(PlanItem.project_id == project.id))}
    for e in edits:
        item = items.get(e.get("id"))
        if not item:
            continue
        if e.get("quantity") is not None:
            item.quantity = float(e["quantity"])
        if "person_id" in e:
            item.suggested_person_id = e["person_id"]
        if e.get("name"):
            item.name = e["name"].strip()
    blank = [i.name for i in items.values() if i.method == "qty" and not i.quantity]
    if blank:
        raise Invalid(f"Add a quantity for: {', '.join(blank)}")
    jobs = []
    for item in sorted(items.values(), key=lambda i: i.position):
        job = Job(
            project_id=project.id,
            tag=item.tag,
            name=item.name,
            method=item.method,
            unit=item.unit,
            word=item.unit if item.unit in ("sheets", "weeks") else None,
            total=item.quantity if item.method == "qty" else (100 if item.method == "steps" else 1),
            assignee_id=item.suggested_person_id,
            checklist=list(item.checklist or []),
        )
        db.add(job)
        jobs.append(job)
    db.flush()
    project.plan_status = "approved"
    by_person: dict[int, list[Job]] = {}
    for j in jobs:
        if j.assignee_id:
            by_person.setdefault(j.assignee_id, []).append(j)
    for pid, js in by_person.items():
        p = db.get(Person, pid)
        set_bubble(db, p, "ok", f"New job: {js[0].tag}" + (f" +{len(js) - 1}" if len(js) > 1 else ""), "briefcase")
        emit(
            db, project.org_id, "job.assigned", person=p, actor=actor, job_ids=[j.id for j in js], project_id=project.id
        )
    entry = ledger.append_and_commit(
        db,
        org_id=project.org_id,
        kind="plan_approved",
        text=f"{project.short} plan approved · {len(jobs)} jobs",
        actor=actor,
        project_id=project.id,
        extra={
            "items": [{"name": j.name, "quantity": j.total, "unit": j.unit, "assignee_id": j.assignee_id} for j in jobs]
        },
    )
    emit(
        db,
        project.org_id,
        "plan.approved",
        f"You approved the {project.short} plan. Locked as {ledger.fingerprint(entry.hash)}.",
        actor=actor,
        project_id=project.id,
        fingerprint=ledger.fingerprint(entry.hash),
    )
    db.commit()
    return jobs


def create_job(
    db: Session,
    project: Project,
    actor: Person,
    *,
    name: str,
    method: str,
    total: float | None,
    unit: str,
    assignee_id: int | None,
    tag: str | None = None,
    checklist: list[str] | None = None,
) -> Job:
    if not name.strip():
        raise Invalid("Name the job.")
    if method not in ("qty", "steps", "done"):
        raise Invalid("Measured by must be quantity, weighted steps, or done/not done.")
    if method == "qty" and not (total and total > 0):
        raise Invalid("Add a total so progress can be measured.")
    tag = (tag or " ".join(name.split("·")[0].split()[:2]))[:14]
    job = Job(
        project_id=project.id,
        tag=tag,
        name=name.strip(),
        method=method,
        unit=unit if method == "qty" else ("%" if method == "steps" else ""),
        word=unit if unit in ("sheets", "weeks", "sections") else None,
        total=float(total) if method == "qty" else (100.0 if method == "steps" else 1.0),
        checklist=checklist if checklist is not None else (["Photos"] if method == "qty" else []),
    )
    db.add(job)
    db.flush()
    emit(db, project.org_id, "job.created", f"New job on {project.short}: {job.name}.", actor=actor, job_id=job.id)
    db.commit()
    if assignee_id:
        assign_job(db, job, db.get(Person, assignee_id), actor)
    return job


def assign_job(db: Session, job: Job, person: Person | None, actor: Person) -> Job:
    prev = db.get(Person, job.assignee_id) if job.assignee_id else None
    if person is not None and person.status == "removed":
        raise Invalid("That person was removed from the office.")
    job.assignee_id = person.id if person else None
    org_id = job.project.org_id
    if person:
        set_bubble(db, person, "ok", f"New job: {job.tag}", "briefcase")
        emit(
            db,
            org_id,
            "job.assigned",
            f"{person.name} took on {job.name}" + (f" from {prev.name}." if prev and prev.id != person.id else "."),
            person=person,
            actor=actor,
            job_ids=[job.id],
            from_person_id=prev.id if prev else None,
        )
    else:
        emit(
            db,
            org_id,
            "job.unassigned",
            f"{prev.name if prev else 'Nobody'} came off {job.name}. It needs someone.",
            person=prev,
            actor=actor,
            job_ids=[job.id],
        )
    db.commit()
    return job


# ---------------------------------------------------------------- field inputs and reports


def _guess_job(db: Session, person: Person, text: str | None) -> Job | None:
    jobs = list(db.scalars(select(Job).where(Job.assignee_id == person.id).order_by(Job.id)))
    if not jobs:
        return None
    if text:
        low = text.lower()
        for j in jobs:
            if j.tag.lower() in low or j.name.split("·")[0].strip().lower() in low:
                return j
    open_r = db.scalar(
        select(Report).where(Report.person_id == person.id, Report.status == "draft").order_by(Report.id.desc())
    )
    if open_r and any(j.id == open_r.job_id for j in jobs):
        return open_r.job
    return jobs[0]


def open_report(db: Session, person: Person, job: Job) -> Report:
    """Today's working report for this person and job: a draft, or one sent back for changes."""
    r = db.scalar(
        select(Report)
        .where(
            Report.person_id == person.id, Report.job_id == job.id, Report.status.in_(("draft", "changes_requested"))
        )
        .order_by(Report.id.desc())
    )
    if r is None:
        r = Report(person_id=person.id, job_id=job.id, status="draft")
        db.add(r)
        db.flush()
    return r


INPUT_LABEL = {"photo": "photo", "voice": "voice note", "doc": "file", "text": "message"}


def add_input(
    db: Session,
    settings: Settings,
    person: Person,
    *,
    kind: str,
    text: str | None = None,
    upload: Upload | None = None,
    client_id: str | None = None,
    job_id: int | None = None,
    captured_at: datetime | None = None,
    lat: float | None = None,
    lon: float | None = None,
    when: datetime | None = None,
) -> tuple[Input, Report | None, bool]:
    """Store one thing a person sent. Idempotent on client_id (the offline queue may retry)."""
    if kind not in INPUT_LABEL:
        raise Invalid(f"Unknown input kind: {kind}")
    if client_id:
        existing = db.scalar(select(Input).where(Input.person_id == person.id, Input.client_id == client_id))
        if existing:
            return existing, existing.report, False
    if kind == "text" and not (text and text.strip()):
        raise Invalid("Empty message.")
    if kind in ("photo", "doc", "voice") and upload is None and not (kind == "voice" and text):
        raise Invalid(f"A {INPUT_LABEL[kind]} needs a file.")
    job = db.get(Job, job_id) if job_id else _guess_job(db, person, text)
    if job is not None and job.assignee_id != person.id:
        raise Forbidden("That job isn't yours.")
    inp = Input(
        person_id=person.id,
        kind=kind,
        text=(text or None),
        client_id=client_id,
        captured_at=captured_at,
        lat=lat,
        lon=lon,
        created_at=when or utcnow(),
    )
    if upload is not None:
        blob = save_blob(db, settings, upload.data, upload.mime)
        inp.blob_id, inp.filename = blob.id, upload.filename
        if kind == "photo":
            taken, plat, plon = photo_meta(upload.data)
            inp.captured_at = inp.captured_at or taken
            inp.lat = inp.lat if inp.lat is not None else plat
            inp.lon = inp.lon if inp.lon is not None else plon
    report = None
    if job is not None:
        inp.job_id = job.id
        report = open_report(db, person, job)
        inp.report_id = report.id
    db.add(inp)
    if person.status == "invited":
        person.status = "active"
    person.link = "online"
    label = {
        "photo": "a photo",
        "voice": "a voice note" + (f": “{text.strip()[:120]}”" if text else ""),
        "doc": f"{upload.filename if upload else 'a file'}",
        "text": f"a message: “{(text or '').strip()[:140]}”",
    }[kind]
    db.flush()
    emit(
        db,
        person.org_id,
        "input.received",
        f"{person.name} sent {label}",
        person=person,
        kind=kind,
        input_id=inp.id,
        blob_id=inp.blob_id,
        label=_packet_label(kind, text, upload),
        job_id=job.id if job else None,
    )
    icon = {"photo": "image", "voice": "mic", "doc": "file", "text": "chat"}[kind]
    set_bubble(db, person, "input", _packet_label(kind, text, upload), icon)
    db.commit()
    return inp, report, True


def _packet_label(kind: str, text: str | None, upload: Upload | None) -> str:
    if kind == "photo":
        return "Photo"
    if kind == "voice":
        return "Voice note"
    if kind == "doc":
        return (upload.filename if upload else "File")[:40]
    return f"“{(text or '').strip()[:34]}”"


def redraft(db: Session, settings: Settings, report: Report) -> Report:
    """Re-read the report's inputs (local model or rules) and show the person the new draft."""
    person = report.person
    drafting.draft(db, settings, report)
    report.updated_at = utcnow()
    if report.status == "draft":
        if report.delta:
            set_bubble(
                db,
                person,
                "work",
                f"Draft ready · {fmt_qty(report.delta, report.job.word or report.job.unit, sign=True)}",
            )
        elif report.question:
            set_bubble(db, person, "warn", report.question, "chat")
        else:
            set_bubble(db, person, "work", "Adding to today's draft")
    emit(
        db,
        person.org_id,
        "report.drafted",
        person=person,
        report_id=report.id,
        delta=report.delta,
        by=report.drafted_by,
    )
    db.commit()
    return report


def submit_report(db: Session, settings: Settings, report: Report, actor: Person) -> Report:
    if actor.id != report.person_id:
        raise Forbidden("Only the person who made the report can send it.")
    if report.status not in ("draft", "changes_requested"):
        raise Invalid("This report was already sent.")
    if not report.inputs:
        raise Invalid("Nothing to send yet. Snap a photo or tell me what you did today.")
    drafting.draft(db, settings, report)
    report.status = "submitted"
    report.submitted_at = utcnow()
    report.review = review_mod.review(db, settings, report)
    job = report.job
    change = fmt_qty(report.delta, job.unit, sign=True) if report.delta else report.summary
    set_bubble(db, actor, "pend", f"Submitted · waiting for {manager_name(db, actor.org_id).split(' ')[0]}")
    emit(
        db,
        actor.org_id,
        "report.submitted",
        f"{actor.name}'s Assistant sent a report to the in-tray: {change}"
        + (f", Sta {report.sta_from} to {report.sta_to}" if report.sta_from else ""),
        person=actor,
        report_id=report.id,
        change=change,
        group=report.review["group"],
        job_id=job.id,
    )
    if report.review["group"] == "flagged":
        pm_name = manager_name(db, actor.org_id)
        emit(
            db,
            actor.org_id,
            "report.flagged",
            f"{pm_name}'s Assistant flagged {actor.name}'s report: {report.review['verdict']}",
            person=actor,
            report_id=report.id,
        )
    db.commit()
    return report


def _require_manager(actor: Person) -> None:
    if actor.role not in MANAGER_ROLES:
        raise Forbidden("Only the owner or a project manager can decide on reports.")


def approve_report(db: Session, report: Report, actor: Person, *, at: datetime | None = None) -> LedgerEntry:
    """A human manager approves. Writes the ledger entry; that is what moves progress."""
    _require_manager(actor)
    if report.status != "submitted":
        raise Invalid("Only submitted reports can be approved.")
    job = report.job
    reporter = report.person
    report.status, report.decided_at, report.decided_by_id = "approved", at or utcnow(), actor.id
    text = f"{job.tag} · {fmt_qty(report.delta, job.unit, sign=True)}" + (
        f" · Sta {report.sta_from} to {report.sta_to}" if report.sta_from else ""
    )
    set_bubble(db, reporter, "ok", "Approved · locked", "lock")
    entry = ledger.append_and_commit(
        db,
        org_id=job.project.org_id,
        kind="report_approved",
        text=text,
        actor=actor,
        project_id=job.project_id,
        job_id=job.id,
        report_id=report.id,
        delta=report.delta,
        extra={
            "reporter_id": reporter.id,
            "reporter_name": reporter.name,
            "sta_from": report.sta_from,
            "sta_to": report.sta_to,
            "evidence_sha256": sorted(i.blob.sha256 for i in report.inputs if i.blob),
            "flags": report.review.get("flags", []),
        },
        at=at,
    )
    report.ledger_entry_id = entry.id
    who = "You" if actor.role == "owner" else actor.name
    emit(
        db,
        job.project.org_id,
        "report.approved",
        f"{who} approved {text}. Locked as {ledger.fingerprint(entry.hash)}.",
        person=reporter,
        actor=actor,
        report_id=report.id,
        job_id=job.id,
        project_id=job.project_id,
        fingerprint=ledger.fingerprint(entry.hash),
        change=fmt_qty(report.delta, job.unit, sign=True),
    )
    db.commit()
    return entry


def request_changes(db: Session, report: Report, actor: Person, *, items: list[str], note: str | None) -> Report:
    _require_manager(actor)
    if report.status != "submitted":
        raise Invalid("Only submitted reports can be sent back.")
    report.status, report.decided_at, report.decided_by_id = "changes_requested", utcnow(), actor.id
    items = [i for i in items if i.strip()] or ["A closer look"]
    report.change_note = f"Needs: {', '.join(items)}" + (f". “{note.strip()}”" if note and note.strip() else "")
    report.review = {**report.review, "requested": items}
    set_bubble(db, report.person, "warn", f"Changes requested · {len(items)}", "alert")
    emit(
        db,
        report.person.org_id,
        "report.changes_requested",
        f"{'You' if actor.role == 'owner' else actor.name} sent {report.job.tag} back: {report.change_note}",
        person=report.person,
        actor=actor,
        report_id=report.id,
    )
    db.commit()
    return report


def ask_about_report(db: Session, report: Report, actor: Person, text: str) -> Report:
    _require_manager(actor)
    if not text.strip():
        raise Invalid("Type a question.")
    report.manager_questions = [
        *report.manager_questions,
        {"text": text.strip(), "by": actor.name, "at": utcnow().isoformat() + "Z"},
    ]
    set_bubble(db, report.person, "warn", f"{first_name(actor)} asked 1 question", "chat")
    emit(
        db,
        actor.org_id,
        "report.question",
        f"{'You' if actor.role == 'owner' else actor.name} asked {report.person.name}: “{text.strip()}”",
        person=report.person,
        actor=actor,
        report_id=report.id,
    )
    db.commit()
    return report


def amend_entry(
    db: Session,
    entry: LedgerEntry,
    actor: Person,
    *,
    text: str,
    delta: float | None,
    reason: str,
    at: datetime | None = None,
) -> LedgerEntry:
    _require_manager(actor)
    if not reason.strip():
        raise Invalid("Add a reason. Amendments always say why.")
    if entry.kind == "amendment":
        raise Invalid("Amend the original entry, not an amendment.")
    previous = db.scalar(select(LedgerEntry).where(LedgerEntry.amends_id == entry.id).order_by(LedgerEntry.seq.desc()))
    stations = find_station_range(text) or (entry.payload.get("sta_from"), entry.payload.get("sta_to"))
    new = ledger.append_and_commit(
        db,
        org_id=entry.org_id,
        kind="amendment",
        text=text.strip() or entry.text,
        actor=actor,
        project_id=entry.project_id,
        job_id=entry.job_id,
        delta=delta if delta is not None else (previous or entry).delta,
        amends_id=entry.id,
        extra={
            "reason": reason.strip(),
            "replaces_hash": (previous or entry).hash,
            "reporter_id": entry.payload.get("reporter_id"),
            "sta_from": stations[0],
            "sta_to": stations[1],
        },
        at=at,
    )
    emit(
        db,
        entry.org_id,
        "ledger.amended",
        f"{'You' if actor.role == 'owner' else actor.name} amended a locked entry: {reason.strip()}",
        actor=actor,
        entry_id=entry.id,
        project_id=entry.project_id,
        fingerprint=ledger.fingerprint(new.hash),
    )
    db.commit()
    return new
