"""Entry point aplikasi V2.

Jalankan:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010

Aplikasi V2 berdiri sendiri: database, ekspor, log, dan konfigurasinya
terpisah dari V1 dan tidak menyentuh artefak V1 maupun folder model.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import config
from app.api import admin as admin_api
from app.api import auth as auth_api
from app.api import dosen as dosen_api
from app.api import health as health_api
from app.api import public_attendance as public_api
from app.db.engine import db, init_db
from app.face.engine import engine
from app.web import pages


def _setup_logging() -> None:
    config.ensure_dirs()
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    log = logging.getLogger("absensi_v2")
    init_db()
    log.info("Database V2 siap: %s", config.DATABASE_PATH)

    if config.LOAD_ENGINE_ON_STARTUP:
        if engine.ensure_loaded():
            log.info("Mesin wajah siap | device=%s | orang=%s | ambang=%.3f",
                     engine.status.device, engine.status.n_persons, engine.status.threshold)
        else:
            log.warning("Mesin wajah TIDAK siap: %s", engine.status.error)
    else:
        log.info("Pemuatan model dilewati (LOAD_ENGINE_ON_STARTUP=false).")

    if not config.SECRET_KEY:
        log.warning("SECRET_KEY kosong â€” memakai kunci sementara per proses. "
                    "Isi SECRET_KEY di .env untuk produksi.")
    yield


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

pages.register_static(app)
app.include_router(health_api.router)
app.include_router(auth_api.router)
app.include_router(public_api.router)
app.include_router(admin_api.router)
app.include_router(dosen_api.router)
app.include_router(pages.router)


@app.exception_handler(500)
async def handle_500(request: Request, exc: Exception):
    return JSONResponse(
        {"ok": False, "message": "Terjadi kesalahan internal pada server."},
        status_code=500,
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self)")
    if request.url.path.startswith("/admin") or request.url.path.startswith("/dosen"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def first_run_guard(request: Request, call_next):
    """Sebelum ada admin aktif, arahkan seluruh halaman (kecuali /setup & /health)."""
    path = request.url.path
    if path.startswith("/static") or path in ("/health", "/api/health"):
        return await call_next(request)
    try:
        from app.db.engine import count_admins

        if count_admins() == 0 and path not in ("/setup",):
            if path.startswith("/api/"):
                return JSONResponse(
                    {"ok": False, "message": "Sistem belum dikonfigurasi. Buat admin di /setup."},
                    status_code=503,
                )
            return RedirectResponse("/setup", status_code=303)
    except Exception:
        pass
    return await call_next(request)

