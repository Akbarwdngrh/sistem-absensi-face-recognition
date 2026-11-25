"""API autentikasi: status sesi, logout (JSON), serta info engine.

Login/logout berbasis form ada di `app/web/pages.py` (Halaman HTML).
Endpoint di sini dipakai oleh frontend dashboard untuk memeriksa sesi.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import config
from app.core import deps
from app.face.engine import engine

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def me(request: Request):
    """Informasi pengguna yang sedang login (tanpa secret)."""
    user = deps.current_user(request)
    if not user:
        return {"authenticated": False, "role": None}
    return {
        "authenticated": True,
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "nama": user["nama"],
        "email": user.get("email"),
        "csrf": deps.csrf_token(request),
    }


@router.post("/logout")
def logout(request: Request):
    """Akhiri sesi saat ini."""
    from app.db.engine import audit, db

    user = deps.current_user(request)
    if user:
        db.execute("DELETE FROM auth_sessions WHERE token = ?", (user["session_token"],))
        audit(user["id"], "logout", "users", user["id"], ip=deps.client_ip(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(config.COOKIE_NAME)
    return resp


@router.get("/engine")
def engine_status():
    """Status mesin wajah (tanpa path berkas internal)."""
    st = engine.status
    return {
        "ready": st.loaded,
        "error": st.error,
        "device": st.device,
        "persons": st.n_persons,
        "threshold": st.threshold,
        "max_faces_per_frame": config.MAX_FACES_PER_FRAME,
    }

