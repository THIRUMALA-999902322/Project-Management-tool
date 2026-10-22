"""Database tables.

Naming follows the product: a Person is a real human; their Assistant is not a row, it is
the behaviour of the system acting on that person's inputs. Reports move
draft -> submitted -> (changes_requested | approved). Approval writes a LedgerEntry, and
progress is computed from the ledger only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fieldbook.db import Base, utcnow

ROLES = ("owner", "pm", "engineer", "cad", "qaqc", "crew", "inspector", "contractor")
MANAGER_ROLES = ("owner", "pm")
FIELD_ROLES = ("crew", "inspector", "contractor")
REPORT_STATES = ("draft", "submitted", "changes_requested", "approved")
INPUT_KINDS = ("photo", "voice", "doc", "text")
JOB_METHODS = ("qty", "steps", "done")


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    company: Mapped[str | None] = mapped_column(String(200))  # contractors: their firm
    contact: Mapped[str | None] = mapped_column(String(200))
    device: Mapped[str] = mapped_column(String(20), default="phone")
    location: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # invited | active | removed
    link: Mapped[str] = mapped_column(String(20), default="online")  # online | offline (reported by the field app)
    queued: Mapped[int] = mapped_column(Integer, default=0)
    look: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    bubble_kind: Mapped[str] = mapped_column(String(20), default="idle")
    bubble_text: Mapped[str] = mapped_column(String(200), default="Waiting for the first message")
    bubble_icon: Mapped[str | None] = mapped_column(String(30))
    hired_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Token(Base):
    """Login and field-app links. Only the SHA-256 of the token is stored."""

    __tablename__ = "tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # session | field
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    short: Mapped[str] = mapped_column(String(60))
    client: Mapped[str | None] = mapped_column(String(200))
    color: Mapped[str] = mapped_column(String(9), default="#1F6F8B")
    work_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    plan_status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | reading | review | approved
    plan_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    members: Mapped[list[ProjectMember]] = relationship(cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), primary_key=True)


class Blob(Base):
    """Content-addressed file. The same bytes are stored once, which is also how duplicates are found."""

    __tablename__ = "blobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(String(500))
    dhash: Mapped[str | None] = mapped_column(String(16))  # perceptual hash for photos
    pages: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Document(Base):
    """A scope document uploaded to a project (plans, specs, bid forms)."""

    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    blob_id: Mapped[int] = mapped_column(ForeignKey("blobs.id"))
    filename: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready | duplicate
    duplicate_of: Mapped[str | None] = mapped_column(String(300))
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    blob: Mapped[Blob] = relationship()


class PlanItem(Base):
    """An item the Assistant proposed for a project's plan; becomes a Job when the plan is approved."""

    __tablename__ = "plan_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    tag: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(10), default="qty")
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="")
    source: Mapped[str] = mapped_column(String(300))
    found_in_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    checklist: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggested_person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    tag: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(300))
    method: Mapped[str] = mapped_column(String(10), default="qty")
    unit: Mapped[str] = mapped_column(String(20), default="")
    word: Mapped[str | None] = mapped_column(String(30))  # plain-language unit, e.g. "services"
    total: Mapped[float] = mapped_column(Float, default=1)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), index=True)
    note: Mapped[str | None] = mapped_column(String(200))  # manual status line, e.g. "Next one due Thursday"
    note_tone: Mapped[str | None] = mapped_column(String(10))
    checklist: Mapped[list[str]] = mapped_column(JSON, default=list)  # evidence each report must carry
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    project: Mapped[Project] = relationship()


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # Observations. The Assistant reports these; code computes `delta` from them.
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    sta_from: Mapped[str | None] = mapped_column(String(20))
    sta_to: Mapped[str | None] = mapped_column(String(20))
    delta: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    claims: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing: Mapped[list[str]] = mapped_column(JSON, default=list)
    question: Mapped[str | None] = mapped_column(String(300))  # the Assistant's clarifying question
    drafted_by: Mapped[str] = mapped_column(String(20), default="rules")  # rules | claude
    review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manager_questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    change_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    ledger_entry_id: Mapped[int | None] = mapped_column(Integer)  # no FK: the ledger points at reports, not back
    inputs: Mapped[list[Input]] = relationship(back_populates="report", order_by="Input.id")
    job: Mapped[Job] = relationship()
    person: Mapped[Person] = relationship(foreign_keys=[person_id])


class Input(Base):
    """Something a real person sent to their Assistant."""

    __tablename__ = "inputs"
    __table_args__ = (UniqueConstraint("person_id", "client_id", name="uq_input_client_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    text: Mapped[str | None] = mapped_column(Text)  # message text or voice transcript
    blob_id: Mapped[int | None] = mapped_column(ForeignKey("blobs.id"))
    filename: Mapped[str | None] = mapped_column(String(300))
    client_id: Mapped[str | None] = mapped_column(String(64))  # idempotency key from the field app's offline queue
    captured_at: Mapped[datetime | None] = mapped_column(DateTime)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    blob: Mapped[Blob | None] = relationship()
    report: Mapped[Report | None] = relationship(back_populates="inputs")


class LedgerEntry(Base):
    """Append-only, hash-chained record of approvals, plans and amendments.

    `hash = sha256(prev_hash + canonical_json(payload))`. Database triggers reject UPDATE and
    DELETE. Corrections are new rows with `amends_id` set.
    """

    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, unique=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"))
    amends_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_entries.id"))
    kind: Mapped[str] = mapped_column(String(30))  # baseline | report_approved | plan_approved | amendment
    text: Mapped[str] = mapped_column(Text)
    delta: Mapped[float | None] = mapped_column(Float)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    actor_name: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Event(Base):
    """The field book: an activity feed, and the source of the office's live animations."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UsageLog(Base):
    """One row per Claude API call: what it was for and what it cost in tokens."""

    __tablename__ = "usage_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_reason: Mapped[str | None] = mapped_column(String(30))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
