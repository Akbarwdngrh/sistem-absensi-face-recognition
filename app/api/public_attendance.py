"""API absensi PUBLIK (mahasiswa tanpa login).

Mahasiswa pada V2 tidak memiliki akun. Alurnya:

  1. GET  /api/public/sessions        â†’ daftar sesi yang sedang dibuka (tanpa PII)
  2. POST /api/public/attendance/frame â†’ kirim frame kamera; backend mendeteksi
     SEMUA wajah, mengenali tiap wajah secara independen, lalu mencatat
     kehadiran yang sah.

Aturan yang ditegakkan server:
  * sesi harus `buka` dan kelas harus `aktif`;
  * hanya mahasiswa yang **terdaftar di kelas** yang tercatat;
  * satu mahasiswa satu catatan per sesi (unique constraint + transaksi);
  * wajah tak dikenal tidak menghasilkan catatan;
  * embedding/identitas penuh tidak dikirim ke browser publik (disamarkan).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app import config
from app.core import deps, security
from app.db.engine import audit
from app.face import pipeline
from app.face.engine import engine
from app.services import attendance as svc_attendance
from app.services import kelas as svc_kelas
from app.services import mahasiswa as svc_mahasiswa

router = APIRouter(prefix="/api/public", tags=["public-attendance"])

# Nama-nama pada halaman publik disamarkan: halaman ini dapat diakses siapa pun.
_MASK_VISIBLE = 1


def mask_name(nama: str) -> str:
    """Sembunyikan nama sebagian di halaman publik."""
    if not nama:
        return "Mahasiswa"
    parts = [p for p in nama.split() if p]
    if not parts:
        return "Mahasiswa"
    if len(parts) == 1:
        return parts[0][:_MASK_VISIBLE] + "."
    return f"{parts[0]} {parts[-1][0]}."


def mask_nim(nim: str) -> str:
    if not nim:
        return ""
    return "*" * max(0, len(nim) - 4) + nim[-4:]


@router.get("/sessions")
def sessions_open(request: Request):
    """Sesi absensi yang sedang dibuka. Tidak memuat data mahasiswa."""
    if not config.PUBLIC_ATTENDANCE_ENABLED:
        raise HTTPException(status_code=403, detail="Absensi publik sedang dinonaktifkan.")
    rows = svc_kelas.sesi_publik_aktif()
    return {
        "ok": True,
        "sessions": [
            {
                "id": r["id"],
                "kelas": r["label"],
                "mata_kuliah": r["nama_mk"],
                "kode_mk": r["kode_mk"],
                "tanggal": r["tanggal"],
                "mulai_at": r["mulai_at"],
                "selesai_at": r.get("selesai_at"),
                "keterangan": r.get("keterangan"),
                "n_anggota": r.get("n_anggota", 0),
                "tahun_akademik": r.get("tahun_akademik"),
                "semester": r.get("semester"),
            }
            for r in rows
        ],
    }


@router.get("/sessions/{session_id}")
def session_detail(session_id: int):
    """Detail sesi + validasi bahwa sesi menerima absensi."""
    session = svc_attendance.get_open_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sesi absensi tidak ditemukan.")
    ok, message = svc_attendance.session_accepts_attendance(session)
    return {
        "ok": True,
        "session": {
            "id": session["id"],
            "kelas": session["label"],
            "mata_kuliah": session["nama_mk"],
            "kode_mk": session["kode_mk"],
            "tanggal": session["tanggal"],
            "mulai_at": session["mulai_at"],
            "status": session["status"],
            "menerima_absensi": ok,
            "pesan": message,
        },
    }


@router.post("/attendance/frame")
async def attendance_frame(
    request: Request,
    session_id: int = Form(...),
    csrf: str = Form(""),
    image: UploadFile = File(...),
):
    """Proses satu frame kamera berisi satu atau lebih wajah."""
    if not config.PUBLIC_ATTENDANCE_ENABLED:
        return JSONResponse(
            {"ok": False, "reason": "disabled", "message": "Absensi publik sedang dinonaktifkan."},
            status_code=403,
        )

    ip = deps.client_ip(request)
    if not security.rate_limiter.allow(
        f"public:{ip}", config.RATE_LIMIT_PUBLIC_MAX, config.RATE_LIMIT_PUBLIC_WINDOW
    ):
        return JSONResponse(
            {"ok": False, "reason": "rate_limited",
             "message": "Terlalu banyak permintaan. Mohon tunggu sebentar."},
            status_code=429,
        )

    # Proteksi CSRF double-submit untuk endpoint publik ini.
    if not deps.check_public_csrf(request, csrf):
        return JSONResponse(
            {"ok": False, "reason": "csrf", "message": "Sesi formulir kedaluwarsa. Muat ulang halaman."},
            status_code=403,
        )

    session = svc_attendance.get_open_session(session_id)
    if session is None:
        return JSONResponse(
            {"ok": False, "reason": "session_not_found", "message": "Sesi absensi tidak ditemukan."},
            status_code=404,
        )
    accepts, message = svc_attendance.session_accepts_attendance(session)
    if not accepts:
        return JSONResponse(
            {"ok": False, "reason": "session_closed", "message": message}, status_code=409
        )

    data = await image.read()
    if not data:
        return JSONResponse(
            {"ok": False, "reason": "empty", "message": "Frame kosong."}, status_code=400
        )
    if len(data) > config.MAX_FRAME_BYTES:
        return JSONResponse(
            {"ok": False, "reason": "too_large", "message": "Ukuran gambar terlalu besar."},
            status_code=413,
        )
    bgr = pipeline.imread_bytes(data)
    if bgr is None:
        return JSONResponse(
            {"ok": False, "reason": "bad_image", "message": "Format gambar tidak dikenali."},
            status_code=400,
        )

    try:
        result = engine.recognize_all(bgr)
    except RuntimeError:
        return JSONResponse(
            {"ok": False, "reason": "engine_error",
             "message": "Mesin pengenalan wajah belum siap. Hubungi admin."},
            status_code=503,
        )
    except Exception:
        return JSONResponse(
            {"ok": False, "reason": "inference_error",
             "message": "Terjadi kesalahan saat memproses wajah."},
            status_code=500,
        )

    outcomes: list[dict] = []
    for face in result["faces"]:
        outcomes.append(_process_face(session, face, result))

    n_tercatat = sum(1 for o in outcomes if o["status"] == "tercatat")
    return JSONResponse({
        "ok": True,
        "det_status": result.get("det_status"),
        "n_wajah": result.get("n_faces", 0),
        "n_tercatat": n_tercatat,
        "faces": outcomes,
        "latency_ms": result.get("latency_ms"),
        "pesan": _ringkasan(outcomes, n_tercatat),
    })


def _process_face(session: dict, face: dict, result: dict) -> dict:
    """Petakan satu hasil wajah menjadi outcome absensi."""
    base = {
        "index": face.get("index"),
        "box": face.get("box"),
        "similarity": face.get("similarity"),
        "det_status": face.get("det_status"),
        "n_wajah": result.get("n_faces", 0),
        "nama": "",
        "nim": "",
        "status": "tidak_dikenal",
        "message": "Wajah tidak dikenali.",
        "recorded_at": None,
    }

    if not face.get("recognized"):
        base["message"] = "Wajah tidak dikenali. Pastikan pencahayaan cukup."
        return base

    person = face.get("person") or ""
    mhs = svc_mahasiswa.find_by_person_label(person)
    if mhs is None:
        base["message"] = "Wajah dikenali tetapi belum terdaftar pada sistem."
        base["status"] = "belum_terdaftar"
        return base

    outcome = svc_attendance.record_attendance(
        session=session,
        mahasiswa_id=int(mhs["id"]),
        similarity=face.get("similarity"),
        metode="face",
        sumber="kamera",
        det_status=face.get("det_status") or "",
        n_wajah=result.get("n_faces", 0),
        bbox=face.get("box"),
    )

    base.update({
        "nama": mask_name(mhs.get("nama") or ""),
        "nim": mask_nim(mhs.get("nim") or ""),
        "status": outcome.status,
        "message": outcome.message,
        "recorded_at": outcome.recorded_at,
    })
    if outcome.status == "tidak_anggota":
        base["message"] = "Wajah dikenali, tetapi Anda tidak terdaftar di kelas ini."
    return base


def _ringkasan(outcomes: list[dict], n_tercatat: int) -> str:
    if not outcomes:
        return "Tidak ada wajah terdeteksi. Pastikan wajah terlihat jelas."
    if n_tercatat:
        return f"{n_tercatat} kehadiran berhasil dicatat."
    statuses = {o["status"] for o in outcomes}
    if "sudah_tercatat" in statuses:
        return "Absensi Anda sudah tercatat pada sesi ini."
    if "tidak_anggota" in statuses:
        return "Wajah dikenali, tetapi Anda bukan anggota kelas yang dipilih."
    if "belum_terdaftar" in statuses:
        return "Wajah dikenali, tetapi identitas belum terdaftar pada sistem."
    return "Wajah terdeteksi namun belum berhasil dikenali."

