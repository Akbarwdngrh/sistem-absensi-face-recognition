"""Dependency FastAPI: sesi, peran (RBAC), CSRF.

Semua pemeriksaan hak akses berada di sini â€” disembunyikan menu di frontend
hanya lapisan kenyamanan, bukan mekanisme keamanan.
"""

from __future__ import annotations

import secrets
from typing import Optional, Tuple

from fastapi import HTTPException, Request

from app import config
from app.core import security
from app.db.engine import db, now_iso

# Peran yang boleh login. Mahasiswa TIDAK punya akun pada V2.
ROLE_ADMIN = "admin"
ROLE_DOSEN = "dosen"
STAFF_ROLES = (ROLE_ADMIN, ROLE_DOSEN)

# Kunci cadangan bila SECRET_KEY belum diisi (hanya untuk pengembangan).
_FALLBACK_SECRET = secrets.token_urlsafe(48)


def get_secret() -> str:
    return config.SECRET_KEY or _FALLBACK_SECRET


def client_ip(request: Request) -> str:
    """IP klien untuk audit & rate limit (bukan untuk otorisasi)."""
    return security.client_ip(request)


def current_user(request: Request) -> Optional[dict]:
    """Ambil user dari cookie sesi. None bila belum login / kedaluwarsa."""
    token = request.cookies.get(config.COOKIE_NAME)
    if not token:
        return None
    row = db.query_one(
        """SELECT u.id, u.username, u.role, u.nama, u.email, u.nim, u.aktif,
                  s.token AS session_token, s.expires_at
           FROM auth_sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, now_iso()),
    )
    if not row or not row.get("aktif"):
        return None
    return row


def require_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Belum masuk")
    return user


def require_roles(request: Request, *roles: str) -> dict:
    user = require_user(request)
    if user["role"] not in roles:
        raise HTTPException(status_code=403, detail="Akses ditolak")
    return user


def require_admin(request: Request) -> dict:
    return require_roles(request, ROLE_ADMIN)


def require_staff(request: Request) -> dict:
    return require_roles(request, ROLE_ADMIN, ROLE_DOSEN)


def csrf_token(request: Request) -> str:
    """Token CSRF untuk form terautentikasi."""
    user = current_user(request)
    if not user:
        return ""
    return security.csrf_for(get_secret(), user["session_token"])


def check_csrf(request: Request, provided: str) -> None:
    """Validasi CSRF untuk request terautentikasi."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Belum masuk")
    if not security.csrf_matches(get_secret(), user["session_token"], provided or ""):
        raise HTTPException(status_code=403, detail="Token CSRF tidak valid")


def public_csrf(request: Request) -> Tuple[str, bool]:
    """Ambil token double-submit untuk halaman publik.

    Mengembalikan (token, perlu_set_cookie).
    """
    token = request.cookies.get(config.CSRF_COOKIE_NAME) or ""
    if len(token) < 24:
        return security.new_public_csrf(), True
    return token, False


def check_public_csrf(request: Request, provided: str) -> bool:
    cookie = request.cookies.get(config.CSRF_COOKIE_NAME) or ""
    if not cookie or not provided:
        return False
    return security.constant_time_equals(cookie, provided)

