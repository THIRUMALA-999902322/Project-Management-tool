import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from fieldbook import ledger
from fieldbook.models import LedgerEntry, Org


def _org(db):
    org = Org(name="Test Co")
    db.add(org)
    db.commit()
    return org


def test_chain_links_each_entry_to_the_previous(db):
    org = _org(db)
    a = ledger.append_and_commit(db, org_id=org.id, kind="baseline", text="A", actor=None, delta=10)
    b = ledger.append_and_commit(db, org_id=org.id, kind="baseline", text="B", actor=None, delta=5)
    assert a.prev_hash == ledger.GENESIS
    assert b.prev_hash == a.hash
    assert (a.seq, b.seq) == (1, 2)
    assert ledger.verify(db).ok


def test_database_refuses_update_and_delete(db):
    org = _org(db)
    e = ledger.append_and_commit(db, org_id=org.id, kind="baseline", text="A", actor=None, delta=10)
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute(text("UPDATE ledger_entries SET delta = 999 WHERE id = :id"), {"id": e.id})
    db.rollback()
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute(text("DELETE FROM ledger_entries WHERE id = :id"), {"id": e.id})
    db.rollback()


def test_verify_finds_tampering_even_if_triggers_are_dropped(db):
    org = _org(db)
    for n in range(3):
        ledger.append_and_commit(db, org_id=org.id, kind="baseline", text=f"entry {n}", actor=None, delta=n)
    db.execute(text("DROP TRIGGER ledger_no_update"))
    db.execute(text("UPDATE ledger_entries SET text = 'edited' WHERE seq = 2"))
    db.commit()
    db.expire_all()
    v = ledger.verify(db)
    assert not v.ok and v.broken_at_seq == 2


def test_amendment_replaces_value_but_keeps_original(db):
    org = _org(db)
    orig = ledger.append_and_commit(db, org_id=org.id, kind="report_approved", text="+180 LF", actor=None, delta=180)
    ledger.append_and_commit(
        db, org_id=org.id, kind="amendment", text="+160 LF", actor=None, delta=160, amends_id=orig.id
    )
    entries = list(db.scalars(select(LedgerEntry)))
    pairs = ledger.effective_entries(entries)
    assert len(pairs) == 1
    original, amendment = pairs[0]
    assert original.delta == 180 and amendment.delta == 160
    assert ledger.verify(db).ok


def test_fingerprint_format():
    assert ledger.fingerprint("a41f" + "0" * 57 + "9c2") == "#a41f…9c2"
