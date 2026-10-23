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

from fieldbook.assistant import llm, rules
from fieldbook.config import Settings
from fieldbook.models import Document, Person, Project
from fieldbook.storage import pdf_text_pages, read_blob

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


def _pages(pdfs: list[tuple[Document, bytes]]) -> list[tuple[str, int, str]]:
    return [(d.filename, n, text) for d, data in pdfs for n, text in enumerate(pdf_text_pages(data), start=1)]


def _select_pages(pages: list[tuple[str, int, str]], budget: int) -> tuple[list[tuple[str, int, str]], int]:
    """The pages most likely to hold quantities (bid forms, schedules) that fit the model's budget."""
    scored = sorted(
        ((len(QTY_ON_LINE.findall(text)), name, n, text) for name, n, text in pages if text.strip()),
        key=lambda x: -x[0],
    )
    chosen, used = [], 0
    for hits, name, n, text in scored:
        if hits == 0 and chosen:
            break
        if used + len(text) > budget:
            continue
        chosen.append((name, n, text))
        used += len(text)
    return chosen, sum(1 for h, *_ in scored if h) - sum(1 for c in chosen if QTY_ON_LINE.search(c[2]))


def _in_text(quantity: float, text: str) -> bool:
    """Did the documents actually print this number? Guards against a model inventing one."""
    whole = f"{quantity:,.0f}" if float(quantity).is_integer() else f"{quantity:,}"
    return any(v in text for v in {whole, whole.replace(",", ""), f"{quantity:g}"})


def propose(db: Session, settings: Settings, project: Project) -> tuple[list[Proposed], list[str], str]:
    """Returns (items, notes for the owner, "llm" | "rules")."""
    docs = [
        (d, read_blob(settings, d.blob))
        for d in db.scalars(select(Document).where(Document.project_id == project.id, Document.status == "ready"))
    ]
    notes: list[str] = []
    pdfs = [(d, data) for d, data in docs if d.blob.mime == "application/pdf"]
    others = [d.filename for d, _ in docs if d.blob.mime != "application/pdf"]
    if others:
        notes.append(f"Not read (only PDFs are read in this version): {', '.join(others)}")
    pages = _pages(pdfs)
    blank = sum(1 for *_, text in pages if not text.strip())
    if blank:
        notes.append(
            f"{blank} page{'s' if blank != 1 else ''} had no text layer (scanned drawings) and could not be read."
        )

    if settings.use_llm and pages:
        chosen, skipped = _select_pages(pages, settings.llm_max_input_chars)
        if skipped:
            notes.append(
                f"{skipped} more page{'s' if skipped != 1 else ''} with quantities did not fit in one read. "
                "Check the plan against the full bid form, or raise FIELDBOOK_LLM_MAX_INPUT_CHARS for a model with a larger context."
            )
        team = [p for p in db.scalars(select(Person).where(Person.id.in_([m.person_id for m in project.members])))]
        intro = (
            f"Project: {project.name}. Client: {project.client or 'not given'}. "
            f"Kinds of work: {', '.join(project.work_types) or 'not given'}. "
            f"Team roles available: {', '.join(sorted({p.role for p in team})) or 'pm'}.\n\n"
            "Document pages (each starts with its file name and page number, use those for `source`):"
        )
        body = "\n\n".join(f"=== {name} p.{n} ===\n{text}" for name, n, text in chosen)
        out = llm.structured(
            db,
            settings,
            purpose="propose_plan",
            system=SYSTEM,
            content=[llm.text_part(f"{intro}\n\n{body}")],
            output=PlanOut,
        )
        if out is not None:
            checklists = {row[1]: row[6] for t in LIBRARY.values() for row in t} | {row[1]: row[6] for row in ALWAYS}
            items = []
            for i in out.items:
                qty, source = i.quantity, i.source
                if qty is not None and not _in_text(qty, body):
                    qty, source = None, "The model's figure is not in the documents · enter from the plans"
                items.append(
                    Proposed(
                        name=i.name,
                        tag=i.tag[:40],
                        method=i.method if i.method in ("qty", "steps", "done") else "qty",
                        quantity=qty,
                        unit=rules.norm_unit(i.unit) or "",
                        source=source,
                        roles=[i.role],
                        found=qty is not None,
                        checklist=checklists.get(i.tag, ["Photos"] if i.role in ("crew", "contractor") else []),
                    )
                )
            if items:
                return items, notes + out.notes[:3], "llm"
    return _rules_plan(project.work_types, _doc_lines(pdfs)), notes, "rules"
