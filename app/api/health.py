"""Health check & status operasional (tanpa membocorkan path internal)."""

from __future__ import annotations

from fastapi import APIRouter

from app import config
from app.db.engine import db
from app.face.engine import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Status aplikasi. Aman dipanggil tanpa autentikasi."""
    st = engine.status
    try:
        db_ok = db.query_value("SELECT 1", (), 0) == 1
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "database": db_ok,
        "engine_ready": st.loaded,
        "device": st.device,
        "persons": st.n_persons,
        "threshold": st.threshold,
        "public_attendance": config.PUBLIC_ATTENDANCE_ENABLED,
    }


@router.get("/api/health")
def health_api():
    return health()

