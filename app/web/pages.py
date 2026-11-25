"""Halaman web (Jinja2).

Pemisahan peran:
  * Halaman publik  â€” /absen (mahasiswa tanpa login), /login, /setup
  * Halaman staff   â€” /admin (admin), /dosen (dosen)

Otorisasi ditegakkan di backend pada setiap rute; menyembunyikan menu di
frontend hanyalah lapisan kenyamanan.
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config
from app.core import deps, security
from app.db.engine import audit, count_admins, db, now_iso
from app.face.engine import engine
from app.services import dosen as svc_dosen
from app.services import kelas as svc_kelas
from app.services import laporan as svc_laporan

router = APIRouter(tags=["pages"])

TEMPLATES_DIR = config.BASE_DIR / "app" / "web" / "templates"
STATIC_DIR = config.BASE_DIR / "app" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_USERNAME_RE = re.compile(r"^[a-z0-9._]{3,32}$")


# ---------------------------------------------------------------------------
# Helper konteks
# ---------------------------------------------------------------------------
def fmt_waktu(value: Optional[str]) -> str:
    if not value:
        return "-"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(value)
        bulan = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu",
                 "Sep", "Okt", "Nov", "Des"]
        return f"{dt.day} {bulan[dt.month]} {dt.year} {dt.strftime('%H:%M')}"
    except Exception:
        return value


templates.env.filters["waktu"] = fmt_waktu


def base_ctx(request: Request, user: Optional[dict] = None, **extra) -> dict:
    ctx = {
        "request": request,
        "user": user,
        "app_name": config.APP_NAME,
        "app_version": config.APP_VERSION,
        "csrf": deps.csrf_token(request) if user else "",
        "msg": request.query_params.get("msg"),
        "err": request.query_params.get("err"),
        "path": request.url.path,
    }
    ctx.update(extra)
    return ctx


def setup_required() -> bool:
    try:
        return count_admins() == 0
    except Exception:
        return False


def render_public(request: Request, template: str, **extra):
    """Render halaman publik + ensure cookie CSRF double-submit."""
    token, is_new = deps.public_csrf(request)
    resp = templates.TemplateResponse(request, template, base_ctx(request, None, auth_csrf=token, **extra))
    if is_new:
        resp.set_cookie(
            config.CSRF_COOKIE_NAME, token,
            httponly=False, samesite="lax", secure=config.COOKIE_SECURE,
            max_age=6 * 3600, path="/",
        )
    return resp


# ---------------------------------------------------------------------------
# Rute dasar
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    user = deps.current_user(request)
    if setup_required():
        return RedirectResponse("/setup", status_code=303)
    if user is None:
        return RedirectResponse("/absen", status_code=303)
    if user["role"] == "admin":
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/dosen", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if setup_required():
        return RedirectResponse("/setup", status_code=303)
    user = deps.current_user(request)
    if user is not None:
        return RedirectResponse("/admin" if user["role"] == "admin" else "/dosen", status_code=303)
    return render_public(request, "login.html")


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    auth_csrf: str = Form(""),
):
    if setup_required():
        return RedirectResponse("/setup", status_code=303)
    ip = deps.client_ip(request)
    if not security.rate_limiter.allow(
        f"login:{ip}", config.RATE_LIMIT_LOGIN_MAX, config.RATE_LIMIT_LOGIN_WINDOW
    ):
        return RedirectResponse(
            "/login?err=" + _urlenc("Terlalu banyak percobaan masuk. Coba lagi beberapa saat."),
            status_code=303,
        )
    if not deps.check_public_csrf(request, auth_csrf):
        return RedirectResponse("/login?err=" + _urlenc("Sesi formulir kedaluwarsa. Muat ulang halaman."),
                                status_code=303)

    username = (username or "").strip()
    row = db.query_one(
        "SELECT * FROM users WHERE aktif = 1 AND lower(username) = lower(?) LIMIT 1",
        (username,),
    )
    if row is None or not security.verify_password(password, row["password_hash"]):
        audit(None, "login_gagal", "users", None, f"login={username}", ip=ip)
        return RedirectResponse("/login?err=" + _urlenc("Nama pengguna atau kata sandi salah."),
                                status_code=303)

    if security.needs_rehash(row["password_hash"]):
        try:
            db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                       (security.hash_password(password), now_iso(), row["id"]))
        except Exception:
            pass

    token = security.new_session_token()
    db.execute(
        """INSERT INTO auth_sessions (token, user_id, created_at, expires_at, user_agent, ip)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (token, row["id"], now_iso(), security.session_expiry(config.SESSION_HOURS),
         request.headers.get("user-agent", "")[:200], ip),
    )
    audit(row["id"], "login", "users", row["id"], ip=ip)
    security.rate_limiter.reset(f"login:{ip}")
    resp = RedirectResponse("/admin" if row["role"] == "admin" else "/dosen", status_code=303)
    resp.set_cookie(
        config.COOKIE_NAME, token,
        httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
        max_age=config.SESSION_HOURS * 3600, path="/",
    )
    return resp


@router.get("/logout")
def logout(request: Request):
    user = deps.current_user(request)
    if user:
        db.execute("DELETE FROM auth_sessions WHERE token = ?", (user["session_token"],))
        audit(user["id"], "logout", "users", user["id"], ip=deps.client_ip(request))
    resp = RedirectResponse("/login?msg=" + _urlenc("Anda telah keluar."), status_code=303)
    resp.delete_cookie(config.COOKIE_NAME)
    return resp


# ---------------------------------------------------------------------------
# First-run: pembuatan admin pertama
# ---------------------------------------------------------------------------
@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if not setup_required():
        return RedirectResponse("/login?msg=" + _urlenc("Sistem sudah dikonfigurasi."), status_code=303)
    return render_public(request, "setup.html")


@router.post("/setup")
def setup_submit(
    request: Request,
    auth_csrf: str = Form(""),
    nama: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    if not setup_required():
        return RedirectResponse("/login?msg=" + _urlenc("Sistem sudah dikonfigurasi."), status_code=303)
    if not deps.check_public_csrf(request, auth_csrf):
        return RedirectResponse("/setup?err=" + _urlenc("Sesi formulir kedaluwarsa. Muat ulang halaman."),
                                status_code=303)

    nama = " ".join((nama or "").split())
    username = (username or "").strip().lower()
    error = None
    if not nama:
        error = "Nama lengkap wajib diisi."
    elif not _USERNAME_RE.match(username):
        error = "Username hanya huruf/angka/titik/garis bawah (3-32 karakter)."
    else:
        error = security.validate_password(password, config.PASSWORD_MIN_LENGTH,
                                           username=username, nama=nama)
    if not error and password != password2:
        error = "Konfirmasi kata sandi tidak cocok."

    if error:
        return RedirectResponse("/setup?err=" + _urlenc(error), status_code=303)

    ts = now_iso()
    try:
        with db.transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND aktif=1")
            row = cur.fetchone()
            jumlah = int(list(row.values())[0] if isinstance(row, dict) else row[0])
            if jumlah > 0:
                conn.rollback()
                return RedirectResponse("/login?msg=" + _urlenc("Sistem sudah dikonfigurasi."),
                                        status_code=303)
            cur.execute(
                """INSERT INTO users (username, password_hash, role, nama, email, nim, aktif,
                                     created_at, updated_at)
                   VALUES (?, ?, 'admin', ?, NULL, NULL, 1, ?, ?)""",
                (username, security.hash_password(password), nama, ts, ts),
            )
            conn.commit()
    except Exception:
        return RedirectResponse("/setup?err=" + _urlenc("Gagal menyimpan akun admin. Coba lagi."),
                                status_code=303)

    audit(None, "setup_admin", "users", None, f"username={username}",
          ip=deps.client_ip(request))
    return RedirectResponse(
        "/login?msg=" + _urlenc("Akun admin pertama dibuat. Silakan masuk."),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Halaman publik absensi
# ---------------------------------------------------------------------------
@router.get("/absen", response_class=HTMLResponse)
def absen_page(request: Request):
    """Halaman absensi mahasiswa TANPA login."""
    if setup_required():
        return RedirectResponse("/setup", status_code=303)
    if not config.PUBLIC_ATTENDANCE_ENABLED:
        return render_public(request, "absen.html", disabled=True,
                             sessions=[], engine_ready=engine.status.loaded)
    sessions = svc_kelas.sesi_publik_aktif()
    return render_public(
        request, "absen.html",
        sessions=sessions,
        disabled=False,
        engine_ready=engine.status.loaded,
        engine_error=engine.status.error,
    )


# ---------------------------------------------------------------------------
# Dashboard admin
# ---------------------------------------------------------------------------
@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = deps.current_user(request)
    if user is None:
        return RedirectResponse("/login?err=" + _urlenc("Silakan masuk terlebih dahulu."), status_code=303)
    deps.require_roles(request, "admin")
    stats = db.query_one(
        """SELECT
             (SELECT COUNT(*) FROM mahasiswa) AS mahasiswa,
             (SELECT COUNT(*) FROM mahasiswa WHERE aktif=1) AS mhs_aktif,
             (SELECT COUNT(*) FROM dosen) AS dosen,
             (SELECT COUNT(*) FROM kelas) AS kelas,
             (SELECT COUNT(*) FROM sesi_absensi WHERE status='buka') AS sesi_buka,
             (SELECT COUNT(*) FROM kehadiran WHERE status='HADIR') AS hadir,
             (SELECT COUNT(*) FROM kehadiran WHERE metode='manual') AS manual"""
    ) or {}
    return templates.TemplateResponse(
        request, "admin.html",
        base_ctx(request, user, role="admin", stats=stats,
                 engine=engine.status, audit_terbaru=db.query_all(
                     """SELECT a.aksi, a.detail, a.created_at, u.username
                        FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
                        ORDER BY a.id DESC LIMIT 8""")),
    )


# ---------------------------------------------------------------------------
# Dashboard dosen
# ---------------------------------------------------------------------------
@router.get("/dosen", response_class=HTMLResponse)
def dosen_page(request: Request):
    user = deps.current_user(request)
    if user is None:
        return RedirectResponse("/login?err=" + _urlenc("Silakan masuk terlebih dahulu."), status_code=303)
    deps.require_roles(request, "admin", "dosen")
    row = svc_dosen.find_dosen_by_user(int(user["id"])) if user["role"] == "dosen" else None
    dosen_id = int(row["id"]) if row else None
    kelas = svc_dosen.list_pengampu(dosen_id) if dosen_id is not None else []
    scope = {"dosen_id": dosen_id} if dosen_id is not None else None
    rows, total = svc_laporan.rekap(scope=scope)
    hadir = sum(1 for r in rows if r.get("status") == "HADIR")
    sesi = svc_kelas.list_sesi(status="buka")
    if scope:
        allowed = {k["id"] for k in kelas}
        sesi = [s for s in sesi if s["class_id"] in allowed]
    return templates.TemplateResponse(
        request, "dosen.html",
        base_ctx(request, user, role="dosen", kelas=kelas, sesi_buka=sesi,
                 stats={"total_catatan": total, "hadir": hadir, "n_kelas": len(kelas)},
                 terbaru=rows[:10]),
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _urlenc(text: str) -> str:
    from urllib.parse import quote

    return quote(text or "")


def register_static(app: FastAPI) -> None:
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

