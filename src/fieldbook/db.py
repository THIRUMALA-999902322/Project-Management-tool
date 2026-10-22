"""Engine, sessions and schema setup.

SQLite in WAL mode for the MVP. The ledger table gets database triggers that refuse UPDATE
and DELETE, so "approved history is locked" holds even against a bug in the application.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp; SQLite has no time zones, so everything stored is UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - exercised implicitly
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return engine


LEDGER_TRIGGERS_SQLITE = [
    """CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_entries
       BEGIN SELECT RAISE(ABORT, 'ledger is append-only: use an amendment'); END""",
    """CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_entries
       BEGIN SELECT RAISE(ABORT, 'ledger is append-only: entries are never deleted'); END""",
]

LEDGER_TRIGGERS_POSTGRES = [
    """CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'ledger is append-only'; END; $$ LANGUAGE plpgsql""",
    """DROP TRIGGER IF EXISTS ledger_no_change ON ledger_entries""",
    """CREATE TRIGGER ledger_no_change BEFORE UPDATE OR DELETE ON ledger_entries
       FOR EACH ROW EXECUTE FUNCTION ledger_append_only()""",
]


def init_db(engine: Engine) -> None:
    from fieldbook import models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.create_all(engine)
    statements = LEDGER_TRIGGERS_POSTGRES if engine.dialect.name == "postgresql" else LEDGER_TRIGGERS_SQLITE
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    db = factory()
    try:
        yield db
    finally:
        db.close()
