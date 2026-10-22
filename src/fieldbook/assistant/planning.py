"""Propose a project plan from its scope documents.

Each proposed item carries its source ("Sheet D-3", "Bid form p.2"). A quantity the
Assistant could not find in the documents stays blank and is marked, so the owner fills it
in from the plans before approving. Nothing is invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.assistant import client, rules
from fieldbook.config import Settings
from fieldbook.models import Document, Person, Project
from fieldbook.storage import pdf_text_pages, read_blob

MAX_PDF_BYTES = 24_000_000  # stay under the 32 MB request limit after base64
MAX_PDF_PAGES = 550  # the API reads up to 600 pages per request

SYSTEM = """You are a project manager's Assistant setting up a civil engineering project (water, sewer, drainage, paving). Read the scope documents and propose the plan: the measurable work items progress will be tracked against.

For each item give: a plain name, a short tag (2 words max), how it is measured ("qty" for a quantity, "steps" for weighted steps, "done" for done/not done such as a permit), the quantity and unit exactly as the documents state them (bid form or quantity table first), the source as the sheet or page it came from (for example "Sheet D-3" or "Bid form p.2"), and the role that would report it (crew, inspector, contractor, cad, engineer, qaqc or pm).

Only use quantities that appear in the documents. If an item clearly belongs in the plan but its quantity is not in the documents, set quantity to null and say so in source. Keep the plan short: the items a manager would check progress on, not every bid line."""


class PlanItemOut(BaseModel):
    name: str
    tag: str
    method: str = Field(description='"qty", "steps" or "done"')
    quantity: float | None
    unit: str
    source: str
    role: str


class PlanOut(BaseModel):
    items: list[PlanItemOut]
    notes: list[str] = Field(
        description="Anything the owner should know about the documents, e.g. pages that were unreadable"
    )


@dataclass
class Proposed:
    name: str
    tag: str
    method: str
    quantity: float | None
    unit: str
    source: str
    roles: list[str]
    found: bool = False
    checklist: list[str] = field(default_factory=list)


# Standard items per kind of work, with the words that find their quantity in a bid form.
LIBRARY: dict[str, list[tuple[str, str, str, str, list[str], list[str], list[str]]]] = {
    # name, tag, method, unit, keywords (all must appear on the line), roles, checklist
    "Water": [
        ("8-in water main", "Water main", "qty", "LF", ["water"], ["crew"], ["Photos", "Pressure test report"]),
        ("Fire hydrant assemblies", "Hydrants", "qty", "EA", ["hydrant"], ["crew"], ["Photos"]),
    ],
    "Sewer": [
        ("8-in sanitary sewer", "Sewer", "qty", "LF", ["sanitary"], ["crew"], ["Photos", "CCTV report"]),
        ("Sanitary manholes", "Manholes", "qty", "EA", ["manhole"], ["crew"], ["Photos"]),
    ],
    "Drainage": [
        ("18-in RCP storm drain", "RCP", "qty", "LF", ["rcp"], ["crew"], ["Photos"]),
        ("Curb inlets", "Inlets", "qty", "EA", ["inlet"], ["crew"], ["Photos"]),
        ("SW3P inspections", "SW3P", "qty", "weeks", ["sw3p"], ["inspector"], ["Inspection form"]),
    ],
    "Paving": [
        ("Paving restoration", "Paving", "qty", "SY", ["pav"], ["contractor", "crew"], ["Photos"]),
    ],
}
ALWAYS = [
    ("Issued-for-construction sheets", "IFC sheets", "qty", "sheets", ["sheet"], ["cad", "engineer"], ["Sheet PDF"]),
    ("Permit approved", "Permit", "done", "", ["permit"], ["pm"], ["Permit letter"]),
]
QTY_ON_LINE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(LF|L\.F\.|EA|SY|CY|WEEKS?|SHEETS?)\b", re.I)


def _find_in_docs(keywords: list[str], unit: str, texts: list[tuple[str, int, str]]) -> tuple[float, str] | None:
    want = rules.norm_unit(unit)
    for filename, page, line in texts:
        low = line.lower()
        if not all(k in low for k in keywords):
            continue
        for m in QTY_ON_LINE.finditer(line):
            u = rules.norm_unit(m.group(2).replace(".", "").lower())
            if u == want or (want == "weeks" and u and u.startswith("week")):
                return float(m.group(1).replace(",", "")), f"{filename} p.{page}"
    return None


def _rules_plan(work_types: list[str], texts: list[tuple[str, int, str]]) -> list[Proposed]:
    rows = [r for t in work_types for r in LIBRARY.get(t, [])] + ALWAYS
    out = []
    for name, tag, method, unit, keywords, roles, checklist in rows:
        if tag == "Permit" and "Drainage" in work_types:
            name = "Drainage permit approved"
        hit = _find_in_docs(keywords, unit, texts) if method == "qty" else None
        if method == "done":
            out.append(Proposed(name, tag, method, 1, unit, "Done or not done", roles, True, checklist))
        elif hit:
            out.append(Proposed(name, tag, method, hit[0], unit, hit[1], roles, True, checklist))
        else:
            out.append(
                Proposed(
                    name,
                    tag,
                    method,
                    None,
                    unit,
                    "Not found in the documents · enter from the plans",
                    roles,
                    False,
                    checklist,
                )
            )
    return out


def _doc_lines(docs: list[tuple[Document, bytes]]) -> list[tuple[str, int, str]]:
    lines = []
    for doc, data in docs:
        if doc.blob.mime != "application/pdf":
            continue
        for n, page in enumerate(pdf_text_pages(data), start=1):
            lines += [(doc.filename, n, ln) for ln in page.splitlines() if ln.strip()]
    return lines


def propose(db: Session, settings: Settings, project: Project) -> tuple[list[Proposed], list[str], str]:
    """Returns (items, notes for the owner, "claude" | "rules")."""
    docs = [
        (d, read_blob(settings, d.blob))
        for d in db.scalars(select(Document).where(Document.project_id == project.id, Document.status == "ready"))
    ]
    notes: list[str] = []
    pdfs = [(d, data) for d, data in docs if d.blob.mime == "application/pdf"]
    others = [d.filename for d, _ in docs if d.blob.mime != "application/pdf"]
    if others:
        notes.append(f"Not read (only PDFs are read in this version): {', '.join(others)}")

    if settings.use_claude and pdfs:
        size = sum(len(data) for _, data in pdfs)
        pages = sum(d.blob.pages or 0 for d, _ in pdfs)
        if size > MAX_PDF_BYTES or pages > MAX_PDF_PAGES:
            notes.append(
                f"The documents are {pages} pages and {size / 1e6:.0f} MB, more than one read can take. "
                "The plan below comes from the bid form text; upload the bid form and quantity sheets on their own for a full read."
            )
        else:
            team = [p for p in db.scalars(select(Person).where(Person.id.in_([m.person_id for m in project.members])))]
            intro = (
                f"Project: {project.name}. Client: {project.client or 'not given'}. "
                f"Kinds of work: {', '.join(project.work_types) or 'not given'}. "
                f"Team roles available: {', '.join(sorted({p.role for p in team})) or 'pm'}."
            )
            content = [client.pdf_block(data, title=d.filename) for d, data in pdfs]
            content.append({"type": "text", "text": intro})
            out = client.structured(
                db, settings, purpose="propose_plan", system=SYSTEM, content=content, output=PlanOut, effort="high"
            )
            if out is not None:
                checklists = {row[1]: row[6] for t in LIBRARY.values() for row in t} | {
                    row[1]: row[6] for row in ALWAYS
                }
                items = [
                    Proposed(
                        name=i.name,
                        tag=i.tag[:40],
                        method=i.method if i.method in ("qty", "steps", "done") else "qty",
                        quantity=i.quantity,
                        unit=rules.norm_unit(i.unit) or "",
                        source=i.source,
                        roles=[i.role],
                        found=i.quantity is not None,
                        checklist=checklists.get(i.tag, ["Photos"] if i.role in ("crew", "contractor") else []),
                    )
                    for i in out.items
                ]
                return items, notes + out.notes, "claude"
    return _rules_plan(project.work_types, _doc_lines(pdfs)), notes, "rules"
