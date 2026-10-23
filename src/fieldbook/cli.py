"""`fieldbook serve | seed | link | verify | usage`."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import case, func, select

from fieldbook.config import load_settings
from fieldbook.db import init_db, make_engine, make_sessionmaker


def _session(settings):
    engine = make_engine(settings.database_url)
    init_db(engine)
    return make_sessionmaker(engine)()


def cmd_serve(args) -> None:
    import uvicorn

    from fieldbook.app import create_app

    settings = load_settings()
    print(f"Fieldbook on http://{args.host}:{args.port} · Assistant: {settings.assistant_label}")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="info")


def cmd_seed(args) -> None:
    from fieldbook.seed import seed

    settings = load_settings()
    if args.reset and settings.database_url.startswith("sqlite:///"):
        from pathlib import Path

        db_file = Path(settings.database_url.removeprefix("sqlite:///"))
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_file) + suffix).unlink(missing_ok=True)
    db = _session(settings)
    try:
        links = seed(db, settings)
    except RuntimeError as e:
        sys.exit(str(e))
    print("Seeded Northfork Civil · Oak Hollow Water & Sewer Replacement (sample data)\n")
    print(f"  Owner login:   {links['owner_login']}")
    print(f"  Manager login: {links['pm_login']}\n")
    print("  Field app links (one per person; each opens that person's Assistant):")
    for name, url in links["field_links"].items():
        print(f"    {name:<12} {url}")


def cmd_link(args) -> None:
    from fieldbook.models import MANAGER_ROLES, Person
    from fieldbook.security import issue_token

    settings = load_settings()
    db = _session(settings)
    q = args.who.lower()
    people = [p for p in db.scalars(select(Person).where(Person.status != "removed")) if q in (p.name.lower(), p.role)]
    if not people:
        sys.exit(f"No one matches {args.who!r}. Use a full name or a role (owner, pm, crew...).")
    for p in people:
        kind = "session" if p.role in MANAGER_ROLES else "field"
        raw = issue_token(db, p, kind)
        db.commit()
        path = "login" if kind == "session" else "f"
        print(f"{p.name} ({p.role}): {settings.public_url}/{path}/{raw}")


def cmd_verify(_args) -> None:
    from fieldbook.ledger import verify

    v = verify(_session(load_settings()))
    if v.ok:
        print(f"Ledger OK · {v.entries} entries, hash chain intact")
    else:
        sys.exit(f"Ledger BROKEN at seq {v.broken_at_seq}: {v.reason}")


def cmd_doctor(_args) -> None:
    from fieldbook.assistant.llm import check

    settings = load_settings()
    ok, detail = check(settings)
    print(("OK   " if ok else "WARN ") + detail)
    if not ok and settings.llm_model:
        sys.exit(1)


def cmd_usage(_args) -> None:
    from fieldbook.models import UsageLog

    db = _session(load_settings())
    ok = func.sum(case((UsageLog.ok.is_(True), 1), else_=0))
    totals = (func.count(), func.sum(UsageLog.input_tokens), func.sum(UsageLog.output_tokens), ok)
    rows = db.execute(select(UsageLog.purpose, *totals).group_by(UsageLog.purpose)).all()
    if not rows:
        print("No model calls logged yet.")
    for purpose, n, tin, tout, ok in rows:
        print(f"{purpose:<16} calls {n:>4}  ok {ok or 0:>4}  input {tin or 0:>9,}  output {tout or 0:>8,}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fieldbook", description="Fieldbook MVP")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("seed", help="load the sample company from the design brief")
    s.add_argument("--reset", action="store_true", help="delete the SQLite database first")
    s.set_defaults(fn=cmd_seed)
    s = sub.add_parser("link", help="print a new login or field-app link for a person (name or role)")
    s.add_argument("who")
    s.set_defaults(fn=cmd_link)
    sub.add_parser("verify", help="check the ledger's hash chain").set_defaults(fn=cmd_verify)
    sub.add_parser("usage", help="model calls and tokens by purpose").set_defaults(fn=cmd_usage)
    sub.add_parser("doctor", help="check the model server and models").set_defaults(fn=cmd_doctor)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
