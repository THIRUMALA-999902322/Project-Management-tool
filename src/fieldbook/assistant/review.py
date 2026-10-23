"""The manager's Assistant: check a submitted report against the original files and the plan.

Every check that can be code is code (duplicate photos, history, plan overrun, station
continuity, checklist). A local model, when configured, adds what only it can do: look at
the photos and files and say what they show next to what was claimed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.assistant import llm, rules
from fieldbook.config import Settings
from fieldbook.ledger import effective_entries
from fieldbook.models import Input, LedgerEntry, Report
from fieldbook.progress import compute, fmt_num, fmt_qty
from fieldbook.storage import DUP_DISTANCE, downscaled_jpeg, hamming, pdf_text_pages, read_blob

NOTE = "Checked against the original files, not the summary."

SYSTEM = """You are a project manager's Assistant on a civil engineering job. You check one daily report against the original photos and files it came with. List what the evidence actually shows, in short factual lines, and any concern where the evidence does not support a claim. Do not repeat the claims back. Do not approve or reject; the manager decides. Plain construction language."""


class EvidenceOut(BaseModel):
    shows: list[str] = Field(description="What the photos and files show, one short line each")
    concerns: list[str] = Field(description="Claims the evidence does not support; empty if none")


def _usual(db: Session, report: Report) -> float | None:
    """This person's typical approved quantity on this job (amendments applied)."""
    entries = list(
        db.scalars(
            select(LedgerEntry).where(
                LedgerEntry.job_id == report.job_id, LedgerEntry.kind.in_(("report_approved", "amendment"))
            )
        )
    )
    values = [
        float((amend.delta if amend else orig.delta) or 0)
        for orig, amend in effective_entries(entries)
        if orig.kind == "report_approved" and orig.payload.get("reporter_id") == report.person_id
    ]
    return sum(values) / len(values) if len(values) >= 2 else None


def _last_station(db: Session, report: Report) -> str | None:
    entries = list(
        db.scalars(
            select(LedgerEntry).where(
                LedgerEntry.job_id == report.job_id, LedgerEntry.kind.in_(("report_approved", "amendment"))
            )
        )
    )
    best: tuple[float, str] | None = None
    for orig, amend in effective_entries(entries):
        sta = (amend.payload if amend else orig.payload).get("sta_to")
        if sta:
            v = rules.station_value(sta)
            if best is None or v > best[0]:
                best = (v, sta)
    return best[1] if best else None


def _duplicates(db: Session, report: Report) -> list[dict[str, Any]]:
    photos = [i for i in report.inputs if i.kind == "photo" and i.blob and i.blob.dhash]
    if not photos:
        return []
    others = db.execute(
        select(Input, Report)
        .join(Report, Input.report_id == Report.id)
        .where(
            Report.id != report.id,
            Report.status.in_(("submitted", "approved", "changes_requested")),
            Input.kind == "photo",
        )
    ).all()
    out = []
    for idx, inp in enumerate(photos, start=1):
        for other_inp, other_report in others:
            ob = other_inp.blob
            if ob is None or ob.dhash is None:
                continue
            if ob.id == inp.blob_id or hamming(ob.dhash, inp.blob.dhash) <= DUP_DISTANCE:
                when = other_report.submitted_at or other_report.created_at
                out.append(
                    {"photo": idx, "input_id": inp.id, "report_id": other_report.id, "date": f"{when:%b} {when.day}"}
                )
                break
    return out


def review(db: Session, settings: Settings, report: Report) -> dict[str, Any]:
    job = report.job
    photos = [i for i in report.inputs if i.kind == "photo"]
    docs = [i for i in report.inputs if i.kind == "doc"]
    voices = [i for i in report.inputs if i.kind == "voice"]
    flags: list[dict[str, str]] = []
    shows: list[str] = []
    questions: list[str] = []
    unit = job.word or job.unit

    if photos:
        gps = sum(1 for p in photos if p.lat is not None)
        shows.append(
            f"{len(photos)} photo{'s' if len(photos) != 1 else ''}" + (f", {gps} with GPS" if gps else ", no GPS")
        )
    elif any(item.lower() in ("photo", "photos") for item in job.checklist):
        flags.append({"tone": "warn", "text": "No photos attached"})
    if docs:
        shows.append("Files: " + ", ".join(d.filename or "file" for d in docs))
    if voices:
        shows.append(f"{len(voices)} voice note{'s' if len(voices) != 1 else ''}")

    dups = _duplicates(db, report)
    if dups:
        dates = sorted({d["date"] for d in dups})
        flags.append(
            {"tone": "bad", "text": f"{len(dups)} of {len(photos)} photos match ones submitted {', '.join(dates)}"}
        )
        nums = [str(d["photo"]) for d in dups]
        questions.append(f"Can you retake photo{'s' if len(nums) > 1 else ''} {' and '.join(nums)}?")

    if report.quantity is not None and report.sta_from and report.sta_to and rules.norm_unit(job.unit) == "LF":
        span = rules.station_value(report.sta_to) - rules.station_value(report.sta_from)
        if abs(span - report.quantity) > max(1.0, 0.02 * span):
            flags.append(
                {
                    "tone": "warn",
                    "text": f"{fmt_num(report.quantity)} LF claimed, but Sta {report.sta_from} to {report.sta_to} is {fmt_num(span)} LF",
                }
            )

    last = _last_station(db, report)
    if last and report.sta_from and rules.station_value(report.sta_from) < rules.station_value(last):
        flags.append({"tone": "warn", "text": f"Starts at {report.sta_from}, but approved work already reaches {last}"})

    prog = compute(db, [job])[job.id]
    other_pending = prog.pend - (report.delta if report.status == "submitted" else 0)
    if job.method != "done" and prog.done + other_pending + report.delta > job.total + 1e-9:
        flags.append({"tone": "bad", "text": f"Would pass the plan total of {fmt_qty(job.total, job.unit)}"})

    usual = _usual(db, report)
    usual_line = None
    if usual and report.delta:
        usual_line = f"{fmt_qty(report.delta, unit)} today vs. this crew's usual {fmt_qty(round(usual), unit)}"
        if report.delta > 1.5 * usual:
            flags.append({"tone": "warn", "text": f"{report.delta / usual:.1f}× this crew's usual output"})
            questions.append("Anything different about today?")
    elif report.delta:
        usual_line = f"{fmt_qty(report.delta, unit)} today · not enough history to compare yet"

    for m in report.missing:
        questions.append(f"When will the {m[0].lower()}{m[1:]} be in?")
    if report.question:
        questions.append(report.question)

    checked_by = "rules"
    if settings.use_llm and (photos or docs):
        lines = ["Claims:", *[f"- {c}" for c in report.claims or [report.summary]]]
        for d in docs:
            if d.blob and d.blob.mime == "application/pdf":
                excerpt, _ = llm.clip(
                    "\n".join(pdf_text_pages(read_blob(settings, d.blob))), settings.llm_max_input_chars // 3
                )
                if excerpt.strip():
                    lines += [f"File {d.filename}:", excerpt]
        content: list[dict] = [llm.text_part("\n".join(lines))]
        for p in photos[-settings.llm_max_images :]:
            jpeg = downscaled_jpeg(read_blob(settings, p.blob), max_side=896) if p.blob else None
            if jpeg:
                content.append(llm.image_part(jpeg))
        ev = llm.structured(db, settings, purpose="review_evidence", system=SYSTEM, content=content, output=EvidenceOut)
        if ev is not None:
            shows = ev.shows + [s for s in shows if s[0].isdigit()]
            # The model's concerns are opinions, so they are labelled and never flag a report on their own.
            flags += [{"tone": "warn", "text": f"Assistant: {c}"} for c in ev.concerns[:3]]
            checked_by = "llm"

    bad = [f for f in flags if f["tone"] == "bad"]
    warn = [f for f in flags if f["tone"] == "warn"]
    if bad:
        group, verdict = "flagged", bad[0]["text"] + "."
    elif report.missing:
        group = "attention"
        verdict = f"Matches the plan. The {report.missing[0][0].lower()}{report.missing[0][1:]} isn't in yet."
    elif warn:
        group, verdict = "attention", warn[0]["text"] + "."
    else:
        group, verdict = "routine", "Evidence complete. Matches the plan."

    claimed = list(report.claims) or [report.summary]
    return {
        "group": group,
        "verdict": verdict,
        "flags": flags,
        "claimed": claimed,
        "shows": shows or ["Text only, no photos or files"],
        "missing": list(report.missing),
        "usual": usual_line,
        "questions": list(dict.fromkeys(questions))[:4],
        "duplicates": dups,
        "checked_by": checked_by,
        "note": NOTE,
    }
