"""The append-only ledger: the only place progress comes from.

Each entry's hash covers the previous entry's hash and the entry's canonical payload, so any
edit to history breaks the chain and `verify()` reports where. The database refuses UPDATE
and DELETE on the table (see db.py); corrections are amendment entries.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fieldbook.db import iso, utcnow
from fieldbook.models import LedgerEntry, Person

GENESIS = "0" * 64
_append_lock = threading.Lock()


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical(payload)).encode("utf-8")).hexdigest()


def fingerprint(h: str) -> str:
    """Short form shown in the UI, e.g. #a41f…9c2."""
    return f"#{h[:4]}…{h[-3:]}"


def append_and_commit(
    db: Session,
    *,
    org_id: int,
    kind: str,
    text: str,
    actor: Person | None,
    project_id: int | None = None,
    job_id: int | None = None,
    report_id: int | None = None,
    delta: float | None = None,
    amends_id: int | None = None,
    extra: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> LedgerEntry:
    """Append one entry and commit it together with whatever else is pending in `db`.

    Committing inside the lock keeps `seq` and `prev_hash` linear within this process; the
    unique constraints on both columns stop a fork if two processes ever race.
    `at` is only for importing history (the seed); live approvals are stamped now.
    """
    with _append_lock:
        last = db.scalar(select(LedgerEntry).order_by(LedgerEntry.seq.desc()).limit(1))
        seq = last.seq + 1 if last else 1
        prev = last.hash if last else GENESIS
        created = at or utcnow()
        actor_name = actor.name if actor else "System"
        payload: dict[str, Any] = {
            "seq": seq,
            "kind": kind,
            "org_id": org_id,
            "project_id": project_id,
            "job_id": job_id,
            "report_id": report_id,
            "amends_id": amends_id,
            "delta": delta,
            "text": text,
            "actor_id": actor.id if actor else None,
            "actor_name": actor_name,
            "created_at": iso(created),
            **(extra or {}),
        }
        entry = LedgerEntry(
            seq=seq,
            org_id=org_id,
            project_id=project_id,
            job_id=job_id,
            report_id=report_id,
            amends_id=amends_id,
            kind=kind,
            text=text,
            delta=delta,
            actor_id=actor.id if actor else None,
            actor_name=actor_name,
            payload=payload,
            prev_hash=prev,
            hash=chain_hash(prev, payload),
            created_at=created,
        )
        db.add(entry)
        db.commit()
        return entry


@dataclass
class Verification:
    ok: bool
    entries: int
    broken_at_seq: int | None = None
    reason: str | None = None


def verify(db: Session) -> Verification:
    prev = GENESIS
    count = 0
    for entry in db.scalars(select(LedgerEntry).order_by(LedgerEntry.seq)):
        count += 1
        if entry.prev_hash != prev:
            return Verification(False, count, entry.seq, "prev_hash does not match the previous entry")
        if entry.payload.get("seq") != entry.seq or entry.payload.get("text") != entry.text:
            return Verification(False, count, entry.seq, "row does not match its hashed payload")
        if chain_hash(prev, entry.payload) != entry.hash:
            return Verification(False, count, entry.seq, "hash does not match payload")
        prev = entry.hash
    return Verification(True, count)


def effective_entries(entries: list[LedgerEntry]) -> list[tuple[LedgerEntry, LedgerEntry | None]]:
    """Pair each original entry with its latest amendment (if any). Amendments themselves are folded in."""
    latest: dict[int, LedgerEntry] = {}
    for e in sorted(entries, key=lambda x: x.seq):
        if e.kind == "amendment" and e.amends_id:
            latest[e.amends_id] = e
    return [(e, latest.get(e.id)) for e in entries if e.kind != "amendment"]
