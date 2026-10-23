"""FastAPI application: API routers, the office app, the field app, and sign-in links."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from fieldbook import services
from fieldbook.api import field, office, stream
from fieldbook.config import Settings, load_settings
from fieldbook.db import init_db, make_engine, make_sessionmaker
from fieldbook.models import MANAGER_ROLES
from fieldbook.security import SESSION_COOKIE, person_for_token

WEB = Path(__file__).parent / "web"
log = logging.getLogger("fieldbook")

SIGN_IN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fieldbook · Sign in</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#FAF8F3;color:#14213D;
font:16px/1.5 system-ui,sans-serif;padding:16px}main{max-width:420px}h1{font-size:24px;margin:0 0 8px}p{color:#5B6475;margin:0 0 8px}
code{background:#F0ECE4;padding:2px 6px;border-radius:4px}</style></head><body><main><h1>Open your login link</h1>
<p>Fieldbook signs you in with a private link instead of a password. Open the link you were given, or create one on the server with
<code>fieldbook link owner</code>.</p></main></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)

    app = FastAPI(title="Fieldbook", version="0.1.0", summary="AI-assisted work tracking for civil projects")
    app.state.settings = settings
    app.state.sessions = make_sessionmaker(engine)
    app.state.stream_once = False
    if settings.dev_autologin:
        log.warning("FIELDBOOK_DEV_AUTOLOGIN is on: anyone who can reach this server acts as the owner.")

    @app.exception_handler(services.Invalid)
    async def _invalid(_req: Request, exc: services.Invalid):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(services.Forbidden)
    async def _forbidden(_req: Request, exc: services.Forbidden):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    app.include_router(office.router)
    app.include_router(field.router)
    app.include_router(stream.router)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        db = app.state.sessions()
        try:
            person = person_for_token(db, request.cookies.get(SESSION_COOKIE), ("session",))
        finally:
            db.close()
        if (person and person.role in MANAGER_ROLES) or settings.dev_autologin:
            return FileResponse(WEB / "app.html")
        return HTMLResponse(SIGN_IN_PAGE, status_code=401)

    @app.get("/login/{token}", include_in_schema=False)
    def login(token: str):
        db = app.state.sessions()
        try:
            person = person_for_token(db, token, ("session",))
        finally:
            db.close()
        if person is None:
            raise HTTPException(404, "This login link doesn't work any more.")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=settings.public_url.startswith("https"),
            max_age=60 * 60 * 24 * 30,
        )
        return resp

    @app.get("/f/{token}", include_in_schema=False)
    def field_app(token: str):
        return FileResponse(WEB / "field.html")

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"ok": True, "assistant": settings.assistant_label}

    return app
