"""Progress math. Approved progress comes from the ledger; pending from submitted reports.

The two-part bar is `approved_pct` (solid) and `pending_pct` (hatched, "when approved").
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.ledger import effective_entries
from fieldbook.models import Job, LedgerEntry, Person, Report

COUNTED_KINDS = ("baseline", "report_approved")


def fmt_num(n: float) -> str:
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"


def fmt_qty(n: float, unit: str | None, *, sign: bool = False) -> str:
    """'320 LF', '+4 services', '20%'. Percent hugs the number; words and units get a space."""
    num = ("+" if sign and n > 0 else "") + fmt_num(n)
    if not unit:
        return num
    return f"{num}%" if unit == "%" else f"{num} {unit}"


@dataclass
class JobProgress:
    done: float
    pend: float
    approved_pct: int
    pending_pct: int
    tone: str
    status: str
    qty_label: str


def _pcts(done: float, pend: float, total: float) -> tuple[int, int]:
    if total <= 0:
        return 0, 0
    a = round(min(done, total) / total * 100)
    b = round(min(total, done + pend) / total * 100)
    return a, max(a, b)


def _qty_label(job: Job, done: float) -> str:
    if job.method == "done":
        return "Done" if done >= job.total else "Not done yet"
    if job.method == "steps":
        return f"{fmt_num(done)}% of weighted steps"
    return f"{fmt_num(done)} of {fmt_qty(job.total, job.word or job.unit)}"


def _status(
    job: Job, done: float, pend: float, open_reports: list[Report], people: dict[int, Person]
) -> tuple[str, str]:
    if job.assignee_id is None or job.assignee_id not in people:
        return "warn", "Needs someone"
    submitted = [r for r in open_reports if r.status == "submitted"]
    flagged = [r for r in submitted if r.review.get("group") == "flagged"]
    if flagged:
        return "bad", flagged[-1].review.get("verdict", "Flagged for review")
    changes = [r for r in open_reports if r.status == "changes_requested"]
    if changes:
        who = people.get(changes[-1].person_id)
        n = len(changes[-1].review.get("requested", [])) or 1
        return "warn", f"Needs {n} fix{'es' if n > 1 else ''} from {who.name if who else 'the field'}"
    if job.note:
        return job.note_tone or "none", job.note
    missing = [m for r in submitted for m in r.missing]
    if missing:
        return "warn", f"Waiting on {missing[0][0].lower()}{missing[0][1:]}"
    if pend > 0:
        return "pend", f"{fmt_qty(pend, job.word or job.unit)} waiting for approval"
    if done >= job.total:
        return "ok", "Complete"
    if done == 0:
        return "none", "Not started"
    return "none", "In progress"


def compute(db: Session, jobs: list[Job]) -> dict[int, JobProgress]:
    ids = [j.id for j in jobs]
    if not ids:
        return {}
    entries = list(db.scalars(select(LedgerEntry).where(LedgerEntry.job_id.in_(ids))))
    done: dict[int, float] = defaultdict(float)
    for orig, amend in effective_entries(entries):
        if orig.kind in COUNTED_KINDS and orig.job_id is not None:
            done[orig.job_id] += float((amend.delta if amend else orig.delta) or 0)

    open_reports: dict[int, list[Report]] = defaultdict(list)
    for r in db.scalars(
        select(Report).where(Report.job_id.in_(ids), Report.status.in_(("submitted", "changes_requested")))
    ):
        open_reports[r.job_id].append(r)
    pend = {jid: sum(r.delta for r in rs if r.status == "submitted") for jid, rs in open_reports.items()}
    people = {p.id: p for p in db.scalars(select(Person).where(Person.status != "removed"))}

    out: dict[int, JobProgress] = {}
    for job in jobs:
        d, p = done.get(job.id, 0.0), pend.get(job.id, 0.0)
        a, b = _pcts(d, p, job.total)
        tone, text = _status(job, d, p, open_reports.get(job.id, []), people)
        out[job.id] = JobProgress(d, p, a, b, tone, text, _qty_label(job, d))
    return out


def project_pct(progress: list[JobProgress]) -> tuple[int, int]:
    """Equal weights per job for the MVP; weighted plans come later."""
    if not progress:
        return 0, 0
    return (
        round(sum(p.approved_pct for p in progress) / len(progress)),
        round(sum(p.pending_pct for p in progress) / len(progress)),
    )
