"""Turn what a person sent today into a draft report.

The Assistant (a local open-weight model, or the rules engine) produces observations:
quantity as stated, stations, work items, missing evidence, one clarifying question. Code then
computes the progress `delta` from those observations and the job's unit. The model never
writes `delta`, and a number the person stated in words always beats the model's reading.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fieldbook.assistant import llm, rules
from fieldbook.config import Settings
from fieldbook.models import Report
from fieldbook.storage import downscaled_jpeg, pdf_text_pages, read_blob

SYSTEM = """You are the Assistant for one person on a civil engineering team (water, sewer, drainage, paving; design and construction). The person sends you photos, voice-note transcripts, files and short texts during the day. You turn them into observations for a daily progress report that their manager will check against the original files.

Report only what the inputs state or plainly show:
- quantity and unit of work done today, exactly as stated (do not add up days or estimate from photos);
- stations in the form 12+00 when given;
- short claims a manager can verify (what was installed, set, tested, drafted);
- which checklist items have no evidence yet;
- at most one short clarifying question, only when something needed is missing (for example "Which stations?").

If a number is not stated or clearly visible, leave it null. Never invent quantities, stations or test results. Write in plain construction language, no hype."""


class DraftOut(BaseModel):
    quantity: float | None = Field(description="Work done today in the job's unit, as stated; null if not stated")
    unit: str | None = Field(description="Unit of `quantity`, e.g. LF, EA, SY, sheets")
    station_from: str | None = Field(description="Start station like 12+00, or null")
    station_to: str | None = Field(description="End station like 15+20, or null")
    claims: list[str] = Field(description="Short, checkable statements for the report")
    summary: str = Field(description="One line, e.g. '320 LF · Sta 12+00 to 15+20'")
    missing: list[str] = Field(description="Checklist items with no evidence in the inputs")
    question: str | None = Field(description="One short clarifying question, or null")


def _gather(settings: Settings, report: Report):
    texts: list[str] = []
    photos: list[bytes] = []
    docs: list[tuple[str, str, bytes | None]] = []
    voice = 0
    for inp in report.inputs:
        if inp.kind == "text" and inp.text:
            texts.append(inp.text)
        elif inp.kind == "voice":
            voice += 1
            if inp.text:
                texts.append(inp.text)
        elif inp.kind == "photo" and inp.blob:
            photos.append(read_blob(settings, inp.blob))
            if inp.text:
                texts.append(inp.text)
        elif inp.kind == "doc" and inp.blob:
            data = read_blob(settings, inp.blob)
            body = "\n".join(pdf_text_pages(data)) if inp.blob.mime == "application/pdf" else ""
            docs.append((inp.filename or "file", body, data if inp.blob.mime == "application/pdf" else None))
    return texts, photos, docs, voice


def compute_delta(report: Report) -> float:
    """Progress this report would add once approved. Pure arithmetic over observations."""
    job = report.job
    unit = rules.norm_unit(report.unit)
    job_unit = rules.norm_unit(job.unit)
    if job.method == "done":
        return 1.0 if report.quantity and report.quantity >= 1 else 0.0
    if report.quantity is not None and (unit == job_unit or unit is None or job.method == "steps"):
        return max(0.0, float(report.quantity))
    if job_unit == "LF" and report.sta_from and report.sta_to:
        return max(0.0, rules.station_value(report.sta_to) - rules.station_value(report.sta_from))
    return 0.0


def _model_content(report: Report, texts, photos, docs, voice, settings: Settings) -> list[dict]:
    job = report.job
    lines = [
        f"Job: {job.name} (measured in {job.unit or 'steps'}; plan total {job.total:g}).",
        f"Evidence checklist for this job: {', '.join(job.checklist) or 'photos'}.",
        f"Photos attached: {len(photos)}. Voice notes: {voice}. Files: {', '.join(d[0] for d in docs) or 'none'}.",
        "Messages and voice-note transcripts, oldest first:",
        *[f"- {t}" for t in texts],
    ]
    if report.manager_questions:
        lines.append("Questions the manager asked about this report:")
        lines += [f"- {q['text']}" for q in report.manager_questions]
    budget = settings.llm_max_input_chars
    body, _ = llm.clip("\n".join(lines), budget // 2)
    for name, text, _pdf in docs:
        if text.strip():
            excerpt, _ = llm.clip(text, max(500, budget // (2 * len(docs))))
            body += f"\n\nFile {name}:\n{excerpt}"
    content: list[dict] = [llm.text_part(body)]
    for data in photos[-settings.llm_max_images :]:
        jpeg = downscaled_jpeg(data, max_side=896)
        if jpeg:
            content.append(llm.image_part(jpeg))
    return content


STATION_OK = re.compile(r"^\d{1,3}\+\d{2}(\.\d+)?$")


def draft(db: Session, settings: Settings, report: Report) -> Report:
    """Fill the report's observations from its inputs and compute its delta."""
    job = report.job
    texts, photos, docs, voice = _gather(settings, report)
    base = rules.draft_from_inputs(
        job_name=job.name,
        job_unit=job.unit,
        job_word=job.word,
        checklist=job.checklist,
        texts=texts,
        photos=len(photos),
        voice=voice,
        docs=[(name, body) for name, body, _ in docs],
    )
    out = None
    if settings.use_llm and (texts or photos or docs):
        out = llm.structured(
            db,
            settings,
            purpose="draft_report",
            system=SYSTEM,
            content=_model_content(report, texts, photos, docs, voice, settings),
            output=DraftOut,
        )
    if out is not None:
        # Numbers the person stated (found by the rules engine) win; the model fills the gaps.
        if base.quantity is not None:
            report.quantity, report.unit = base.quantity, base.unit or job.unit
        else:
            report.quantity, report.unit = out.quantity, rules.norm_unit(out.unit) or job.unit
        model_stations = (out.station_from, out.station_to)
        if base.station_from:
            report.sta_from, report.sta_to = base.station_from, base.station_to
        elif all(s and STATION_OK.match(s) for s in model_stations):
            report.sta_from, report.sta_to = model_stations
        else:
            report.sta_from = report.sta_to = None
        report.claims, report.summary = out.claims or base.claims, out.summary or base.summary
        # A checklist item the rules engine can see evidence for is not missing, whatever the model said.
        report.missing = [m for m in out.missing if m in base.missing or m not in job.checklist]
        report.question = out.question
        report.drafted_by = "llm"
    else:
        report.quantity, report.unit = base.quantity, base.unit or job.unit
        report.sta_from, report.sta_to = base.station_from, base.station_to
        report.claims, report.summary = base.claims, base.summary
        report.missing, report.question = base.missing, base.question
        report.drafted_by = "rules"
    report.delta = compute_delta(report)
    return report
