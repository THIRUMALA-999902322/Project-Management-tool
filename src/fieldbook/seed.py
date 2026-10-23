"""The sample company from the design brief (Oak Hollow Water & Sewer Replacement).

Built through the same services the API uses, so the approval queue, the review briefs, the ledger
and the progress numbers are all computed, not typed in. Dates are relative to today.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from fieldbook import ledger, services
from fieldbook.config import Settings
from fieldbook.db import utcnow
from fieldbook.events import emit, set_activity
from fieldbook.models import Event, Job, Org, Person, Project, ProjectMember, Report
from fieldbook.sample import make_pdf, make_photo, reencode
from fieldbook.security import issue_token

PEOPLE = [
    (
        "brooks",
        "Dana Brooks",
        "pm",
        {"contact": "dana@northfork.example", "device": "laptop", "location": "Main office"},
    ),
    ("chen", "D. Chen", "cad", {"contact": "dchen@northfork.example", "device": "laptop", "location": "Design studio"}),
    (
        "novak",
        "A. Novak",
        "engineer",
        {"contact": "anovak@northfork.example", "device": "laptop", "location": "Design studio"},
    ),
    ("singh", "R. Singh", "qaqc", {"contact": "rsingh@northfork.example", "device": "laptop", "location": "QA/QC"}),
    ("ortiz", "J. Ortiz", "crew", {"contact": "(555) 201-0147", "location": "On site · Sta 15+20"}),
    ("lee", "K. Lee", "inspector", {"contact": "(555) 201-0188", "location": "On site · MH-5"}),
    ("alvarez", "M. Alvarez", "crew", {"contact": "(555) 201-0163", "location": "On site · Elm Ct"}),
    ("patel", "S. Patel", "inspector", {"contact": "(555) 201-0119", "location": "Outfall 2 · weak signal"}),
    (
        "ward",
        "T. Ward",
        "contractor",
        {"contact": "(555) 201-0190", "company": "Ridgeline Paving", "location": "Ridgeline Paving yard"},
    ),
]

# key, tag, name, person, method, unit, word, total, checklist, note, tone, approved before Fieldbook
JOBS = [
    (
        "lineA",
        "Line A",
        "Line A · 8-in water main",
        "ortiz",
        "qty",
        "LF",
        None,
        3600,
        ["Photos", "Pressure test report"],
        None,
        None,
        1630,
    ),
    (
        "sewer",
        "Sewer",
        "Sewer MH-4 to MH-7 · 8-in sanitary",
        "lee",
        "steps",
        "%",
        None,
        100,
        ["Photos", "CCTV report"],
        None,
        None,
        30,
    ),
    (
        "services",
        "Services",
        "Services and meter boxes",
        "alvarez",
        "qty",
        "EA",
        "services",
        72,
        ["Photos"],
        None,
        None,
        34,
    ),
    (
        "submittal",
        "90% set",
        "90% submittal · water sheets",
        "chen",
        "qty",
        "sheets",
        "sheets",
        60,
        ["Sheet PDF"],
        None,
        None,
        36,
    ),
    ("calcs", "Specs", "Spec sections · 90% set", "novak", "qty", "sections", "sections", 31, [], None, None, 18),
    ("qa", "QA/QC", "90% set review", "singh", "qty", "sheets", "sheets", 60, [], None, None, 24),
    (
        "sw3p",
        "SW3P",
        "SW3P inspections",
        "patel",
        "qty",
        "weeks",
        "weeks",
        14,
        ["Inspection form"],
        "Next one due Thursday",
        "none",
        6,
    ),
    (
        "paving",
        "Paving",
        "Paving restoration",
        "ward",
        "qty",
        "SY",
        None,
        2150,
        ["Photos"],
        "Starts after Line A pressure test",
        "none",
        0,
    ),
]


def _mark(db: Session) -> int:
    return db.scalar(select(func.max(Event.id))) or 0


def _restamp(db: Session, since: int, when: datetime) -> None:
    db.execute(update(Event).where(Event.id > since).values(created_at=when))
    db.commit()


def _photo(seed: int, label: str, quality: int = 84) -> services.Upload:
    return services.Upload(f"IMG_{seed:04d}.jpg", "image/jpeg", make_photo(seed, label, quality=quality))


def _send_report(
    db: Session,
    settings: Settings,
    person: Person,
    job: Job,
    when: datetime,
    *,
    text: str | None = None,
    voice: str | None = None,
    photos: list[tuple[services.Upload, str]] = (),
    docs: list[tuple[str, list[list[str]]]] = (),
    approve_by: Person | None = None,
) -> tuple[Report, Any]:
    mark = _mark(db)
    gps = (35.0841, -92.4419)
    for n, (upload, caption) in enumerate(photos):
        services.add_input(
            db,
            settings,
            person,
            kind="photo",
            text=caption,
            upload=upload,
            job_id=job.id,
            when=when + timedelta(minutes=n),
            captured_at=when + timedelta(minutes=n),
            lat=gps[0] + n * 1e-4,
            lon=gps[1] + n * 1e-4,
        )
    for name, pages in docs:
        services.add_input(
            db,
            settings,
            person,
            kind="doc",
            upload=services.Upload(name, "application/pdf", make_pdf(pages)),
            job_id=job.id,
            when=when,
        )
    if voice:
        services.add_input(db, settings, person, kind="voice", text=voice, job_id=job.id, when=when)
    if text:
        services.add_input(db, settings, person, kind="text", text=text, job_id=job.id, when=when)
    report = services.open_report(db, person, job)
    services.submit_report(db, settings, report, person)
    report.created_at = report.updated_at = report.submitted_at = when + timedelta(minutes=8)
    db.commit()
    _restamp(db, mark, when + timedelta(minutes=8))
    entry = None
    if approve_by is not None:
        mark = _mark(db)
        decided = when + timedelta(hours=2, minutes=10)
        entry = services.approve_report(db, report, approve_by, at=decided)
        _restamp(db, mark, decided)
    return report, entry


def seed(db: Session, settings: Settings) -> dict[str, Any]:
    if db.scalar(select(Org.id).limit(1)):
        raise RuntimeError("This database already has data. Run `fieldbook seed --reset` to start over.")
    now = utcnow().replace(second=0, microsecond=0)

    def day(offset: int, hour: int, minute: int = 0) -> datetime:
        return (now + timedelta(days=offset)).replace(hour=hour, minute=minute)

    org = Org(name="Northfork Civil")
    db.add(org)
    db.flush()
    owner = Person(
        org_id=org.id,
        name="Alex Morgan",
        role="owner",
        device="laptop",
        location="Main office",
        status="active",
        activity_kind="idle",
        activity_text="Ask me about any job",
    )
    db.add(owner)
    db.flush()
    owner_token = issue_token(db, owner, "session")
    db.commit()

    p: dict[str, Person] = {}
    field_tokens: dict[str, str] = {}
    for key, name, role, kw in PEOPLE:
        person, raw = services.create_person(db, org, name=name, role=role, actor=owner, announce=False, **kw)
        person.status, person.hired_at = "active", None
        p[key], field_tokens[key] = person, raw
    pm_token = issue_token(db, p["brooks"], "session")
    db.commit()
    dana = p["brooks"]

    project = Project(
        org_id=org.id,
        name="Oak Hollow Water & Sewer Replacement",
        short="Oak Hollow",
        client="City of Oak Hollow",
        color="#1F6F8B",
        work_types=["Water", "Sewer", "Paving"],
        plan_status="approved",
    )
    db.add(project)
    db.flush()
    for person in [owner, *p.values()]:
        project.members.append(ProjectMember(person_id=person.id))
    jobs: dict[str, Job] = {}
    for key, tag, name, who, method, unit, word, total, checklist, note, tone, _ in JOBS:
        jobs[key] = Job(
            project_id=project.id,
            tag=tag,
            name=name,
            method=method,
            unit=unit,
            word=word,
            total=total,
            assignee_id=p[who].id,
            checklist=checklist,
            note=note,
            note_tone=tone,
        )
        db.add(jobs[key])
    db.commit()
    ledger.append_and_commit(
        db,
        org_id=org.id,
        kind="plan_approved",
        text=f"Oak Hollow plan approved · {len(JOBS)} jobs",
        actor=dana,
        project_id=project.id,
        at=day(-22, 15),
    )
    for key, *_rest, before in JOBS:
        job = jobs[key]
        if before:
            ledger.append_and_commit(
                db,
                org_id=org.id,
                kind="baseline",
                job_id=job.id,
                project_id=project.id,
                delta=before,
                text=f"{job.tag} · approved before Fieldbook · {before:,} {job.unit}",
                actor=dana,
                at=day(-9, 9),
            )

    # History the review brief compares against.
    _send_report(
        db,
        settings,
        p["ortiz"],
        jobs["lineA"],
        day(-8, 16),
        voice="Laid 180 feet, 5+80 to 7+60.",
        photos=[(_photo(101, "6+40"), "6+40"), (_photo(102, "7+60"), "7+60")],
        approve_by=dana,
    )
    _, entry = _send_report(
        db,
        settings,
        p["ortiz"],
        jobs["lineA"],
        day(-7, 16),
        voice="180 feet today, 7+60 to 9+40.",
        photos=[(_photo(103, "8+20"), "8+20"), (_photo(104, "9+40"), "9+40")],
        approve_by=dana,
    )
    mark = _mark(db)
    services.amend_entry(
        db,
        entry,
        dana,
        text="Line A · +160 LF · Sta 7+80 to 9+40",
        delta=160,
        reason="Re-measured at the tie-in. 20 LF was counted twice.",
        at=day(-6, 9, 5),
    )
    _restamp(db, mark, day(-6, 9, 5))
    _send_report(
        db,
        settings,
        p["ortiz"],
        jobs["lineA"],
        day(-1, 15, 30),
        voice="260 feet, 9+40 to 12+00.",
        photos=[(_photo(105, "10+80"), "10+80"), (_photo(106, "12+00"), "12+00")],
        approve_by=dana,
    )
    old_lee = [make_photo(301, "MH-5"), make_photo(302, "MH-5")]
    _send_report(
        db,
        settings,
        p["lee"],
        jobs["sewer"],
        day(-7, 14),
        text="Pipe laid MH-4 to MH-5. That step is 10%.",
        photos=[
            (services.Upload("IMG_0301.jpg", "image/jpeg", old_lee[0]), "MH-5"),
            (services.Upload("IMG_0302.jpg", "image/jpeg", old_lee[1]), "MH-5"),
        ],
        approve_by=dana,
    )
    _send_report(
        db,
        settings,
        p["alvarez"],
        jobs["services"],
        day(-4, 15),
        text="8 services reconnected on Oak St, lids on.",
        photos=[(_photo(401 + i, "Oak St"), "Oak St") for i in range(3)],
        approve_by=dana,
    )
    _send_report(
        db,
        settings,
        p["alvarez"],
        jobs["services"],
        day(-2, 15),
        text="4 services reconnected, 2 left on Oak St.",
        photos=[(_photo(411 + i, "Oak St"), "Oak St") for i in range(2)],
        approve_by=dana,
    )
    for d_, (a, b) in ((-3, (32, 34)), (-1, (35, 37))):
        _send_report(
            db,
            settings,
            p["chen"],
            jobs["submittal"],
            day(d_, 11),
            text=f"Sheets W-{a} to W-{b} drafted.",
            docs=[(f"W-{a} to W-{b}.pdf", [[f"Sheet W-{n}", "Plan and profile"] for n in range(a, b + 1)])],
            approve_by=dana,
        )

    # Today.
    _send_report(
        db,
        settings,
        p["chen"],
        jobs["submittal"],
        day(0, 10, 5) if now.hour > 11 else now - timedelta(hours=6),
        text="Sheets W-38 to W-41 drafted.",
        docs=[("W-38 to W-41.pdf", [[f"Sheet W-{n}", "Plan and profile, Line A"] for n in range(38, 42)])],
    )
    lee_photos = [(_photo(311 + i, "MH-6"), "MH-6") for i in range(7)]
    lee_photos[3:3] = [
        (services.Upload("IMG_0412.jpg", "image/jpeg", reencode(old_lee[0])), "MH-5"),
        (services.Upload("IMG_0413.jpg", "image/jpeg", reencode(old_lee[1])), "MH-5"),
    ]
    _send_report(
        db,
        settings,
        p["lee"],
        jobs["sewer"],
        now - timedelta(hours=2),
        text="CCTV run MH-4 to MH-7 done, no defects worse than grade 2. CCTV step is 20%.",
        photos=lee_photos,
        docs=[
            (
                "CCTV report MH-4 to MH-7.pdf",
                [
                    ["CCTV inspection report", "Run MH-4 to MH-5: grade 1"],
                    ["Run MH-5 to MH-6: grade 2", "Roots at 2+10"],
                ],
            )
        ],
    )
    _send_report(
        db,
        settings,
        p["alvarez"],
        jobs["services"],
        now - timedelta(minutes=50),
        text="4 services done on Elm Ct, 206 to 212. Meter boxes set, lids on.",
        photos=[(_photo(421 + i, f"{206 + 2 * i} Elm Ct"), f"{206 + 2 * i} Elm Ct") for i in range(4)],
    )
    _send_report(
        db,
        settings,
        p["ortiz"],
        jobs["lineA"],
        now - timedelta(minutes=25),
        voice="Laid 320 feet today, 12+00 to 15+20. Two gate valves set at 13+50 and 15+00. Test crew tomorrow 7 AM.",
        photos=[(_photo(201 + i, s), s) for i, s in enumerate(["12+40", "13+50", "14+60", "15+20"])],
        docs=[("Pipe delivery ticket.pdf", [["Delivery ticket", "8-in C900 PVC, 400 LF"]])],
    )

    for key, text in (
        ("novak", "Marking up spec section 33 11 13 today."),
        ("singh", "Reviewing W-30 to W-36, 12 comments so far."),
    ):
        mark = _mark(db)
        inp, report, _ = services.add_input(db, settings, p[key], kind="text", text=text, when=now - timedelta(hours=1))
        services.redraft(db, settings, report)
        _restamp(db, mark, now - timedelta(hours=1))

    mark = _mark(db)
    patel = p["patel"]
    patel.link, patel.queued = "offline", 12
    set_activity(db, patel, "offline", "Offline · 12 items queued")
    emit(
        db,
        org.id,
        "person.offline",
        "S. Patel went offline at Outfall 2. Items are queued on their phone.",
        person=patel,
    )
    db.commit()
    _restamp(db, mark, now - timedelta(hours=3))

    set_activity(db, dana, "work", "Checking 4 reports")
    set_activity(db, p["lee"], "bad", "Flagged · 2 reused photos")
    set_activity(db, p["novak"], "work", "Marking up spec 33 11 13")
    set_activity(db, p["singh"], "work", "Reviewing W-30 to W-36")
    set_activity(db, p["ward"], "idle", "Waiting on Line A test")
    db.commit()

    base = settings.public_url
    return {
        "owner_login": f"{base}/login/{owner_token}",
        "pm_login": f"{base}/login/{pm_token}",
        "field_links": {p[k].name: f"{base}/f/{t}" for k, t in field_tokens.items()},
    }
