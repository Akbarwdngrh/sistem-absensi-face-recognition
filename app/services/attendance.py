"""Service absensi — pencatatan kehadiran multi-wajah.

Aturan bisnis (semua ditegakkan di BACKEND, bukan frontend):

1. Sesi harus ada, berstatus ``buka``, dan kelasnya ``aktif``.
2. Mahasiswa yang dikenali harus **terdaftar** di kelas tersebut. Bila tidak,
   kehadiran TIDAK dicatat (status ``tidak_anggota``).
3. Satu mahasiswa hanya boleh punya SATU catatan per sesi
   (UNIQUE(session_id, mahasiswa_id) + transaksi eksklusif).
4. Wajah tidak dikenali tidak menghasilkan catatan apa pun.
5. Setiap catatan menyimpan metadata pencocokan yang aman: similarity,
   det_status, jumlah wajah pada frame, dan bounding box.

Tidak ada frame kamera yang disimpan permanen.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.db.engine import db, now_iso

# Cooldown per identitas: mencegah pencatatan berulang pada frame berturut-turut.
_identity_cooldown: dict[tuple[int, int], float] = {}
_cooldown_lock = threading.Lock()


@dataclass
class AttendanceOutcome:
    """Hasil pencatatan untuk satu identitas pada satu frame."""

    mahasiswa_id: Optional[int]
    nama: str
    nim: str
    status: str  # tercatat | sudah_tercatat | tidak_anggota | tidak_dikenal | gagal
    message: str
    similarity: Optional[float] = None
    recorded_at: Optional[str] = None
    session_id: Optional[int] = None
    det_status: str = ""
    n_wajah: int = 0
    bbox: Optional[list] = None

    def to_dict(self) -> dict:
        return {
            "mahasiswa_id": self.mahasiswa_id,
            "nama": self.nama,
            "nim": self.nim,
            "status": self.status,
            "message": self.message,
            "similarity": None if self.similarity is None else round(float(self.similarity), 4),
            "recorded_at": self.recorded_at,
            "session_id": self.session_id,
            "det_status": self.det_status,
            "n_wajah": self.n_wajah,
            "bbox": self.bbox,
        }


# ---------------------------------------------------------------------------
# Validasi sesi
# ---------------------------------------------------------------------------
def get_open_session(session_id: int) -> Optional[dict]:
    """Sesi yang boleh menerima absensi (buka + kelas aktif)."""
    return db.query_one(
        """SELECT s.*, c.kode_mk, c.nama_mk, c.label, c.status AS class_status
           FROM sesi_absensi s JOIN kelas c ON c.id = s.class_id
           WHERE s.id = ?""",
        (session_id,),
    )


def session_accepts_attendance(session: dict) -> tuple[bool, str]:
    if session.get("status") != "buka":
        return False, "Sesi absensi sudah ditutup."
    if session.get("class_status") != "aktif":
        return False, "Kelas ini sedang tidak aktif."
    return True, ""


def is_member(class_id: int, mahasiswa_id: int) -> bool:
    return db.query_one(
        "SELECT 1 AS ok FROM kelas_mahasiswa WHERE class_id = ? AND mahasiswa_id = ?",
        (class_id, mahasiswa_id),
    ) is not None


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------
def _in_cooldown(session_id: int, mahasiswa_id: int) -> bool:
    key = (session_id, mahasiswa_id)
    now = time.monotonic()
    with _cooldown_lock:
        until = _identity_cooldown.get(key)
        if until and until > now:
            return True
        if until:
            _identity_cooldown.pop(key, None)
        return False


def _set_cooldown(session_id: int, mahasiswa_id: int) -> None:
    seconds = max(0, config_identity_cooldown())
    if seconds <= 0:
        return
    with _cooldown_lock:
        _identity_cooldown[(session_id, mahasiswa_id)] = time.monotonic() + seconds


def config_identity_cooldown() -> int:
    from app import config

    return int(config.IDENTITY_COOLDOWN_SECONDS)


# ---------------------------------------------------------------------------
# Pencatatan
# ---------------------------------------------------------------------------
def record_attendance(
    session: dict,
    mahasiswa_id: int,
    similarity: Optional[float],
    metode: str = "face",
    sumber: str = "kamera",
    det_status: str = "",
    n_wajah: int = 0,
    bbox: Optional[list] = None,
    catatan: Optional[str] = None,
    created_by: Optional[int] = None,
) -> AttendanceOutcome:
    """Catat kehadiran secara idempoten (aman terhadap duplikasi & balapan)."""
    session_id = int(session["id"])
    class_id = int(session["class_id"])

    # 1) Validasi keanggotaan kelas (belum boleh catat bila bukan anggota).
    if not is_member(class_id, mahasiswa_id):
        return AttendanceOutcome(
            mahasiswa_id=mahasiswa_id,
            nama="",
            nim="",
            status="tidak_anggota",
            message="Wajah dikenali tetapi mahasiswa ini tidak terdaftar di kelas tersebut.",
            similarity=similarity,
            session_id=session_id,
            det_status=det_status,
            n_wajah=n_wajah,
            bbox=bbox,
        )

    # 2) Sudah tercatat? (cek cepat, tanpa menulis)
    existing = db.query_one(
        "SELECT id, recorded_at, similarity, status FROM kehadiran "
        "WHERE session_id = ? AND mahasiswa_id = ?",
        (session_id, mahasiswa_id),
    )
    if existing:
        return AttendanceOutcome(
            mahasiswa_id=mahasiswa_id,
            nama="",
            nim="",
            status="sudah_tercatat",
            message="Absensi sudah tercatat pada sesi ini.",
            similarity=existing.get("similarity"),
            recorded_at=existing.get("recorded_at"),
            session_id=session_id,
            det_status=det_status,
            n_wajah=n_wajah,
            bbox=bbox,
        )

    # 3) Tulis dalam transaksi eksklusif; UNIQUE(session_id, mahasiswa_id)
    #    menjadi jaring pengaman terakhir bila ada balapan antar request.
    timestamp = now_iso()
    try:
        with db.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO kehadiran
                   (session_id, class_id, mahasiswa_id, status, similarity, metode, sumber,
                    det_status, n_wajah, bbox, catatan, recorded_at, created_by, updated_at)
                   VALUES (?, ?, ?, 'HADIR', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (session_id, mahasiswa_id) DO NOTHING""",
                (
                    session_id, class_id, mahasiswa_id,
                    None if similarity is None else float(similarity),
                    metode, sumber, det_status, n_wajah,
                    _bbox_text(bbox), catatan, timestamp, created_by, timestamp,
                ),
            )
            inserted = cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0
            conn.commit()
    except Exception:
        # Bila gagal (mis. DB terkunci), ambil status sebenarnya.
        existing = db.query_one(
            "SELECT recorded_at, similarity FROM kehadiran WHERE session_id = ? AND mahasiswa_id = ?",
            (session_id, mahasiswa_id),
        )
        if existing:
            return AttendanceOutcome(
                mahasiswa_id=mahasiswa_id, nama="", nim="",
                status="sudah_tercatat",
                message="Absensi sudah tercatat pada sesi ini.",
                similarity=existing.get("similarity"),
                recorded_at=existing.get("recorded_at"),
                session_id=session_id, det_status=det_status, n_wajah=n_wajah, bbox=bbox,
            )
        return AttendanceOutcome(
            mahasiswa_id=mahasiswa_id, nama="", nim="",
            status="gagal", message="Gagal menyimpan absensi.",
            similarity=similarity, session_id=session_id,
            det_status=det_status, n_wajah=n_wajah, bbox=bbox,
        )

    if not inserted:
        existing = db.query_one(
            "SELECT recorded_at, similarity FROM kehadiran WHERE session_id = ? AND mahasiswa_id = ?",
            (session_id, mahasiswa_id),
        )
        return AttendanceOutcome(
            mahasiswa_id=mahasiswa_id, nama="", nim="",
            status="sudah_tercatat",
            message="Absensi sudah tercatat pada sesi ini.",
            similarity=(existing or {}).get("similarity"),
            recorded_at=(existing or {}).get("recorded_at"),
            session_id=session_id, det_status=det_status, n_wajah=n_wajah, bbox=bbox,
        )

    _set_cooldown(session_id, mahasiswa_id)
    return AttendanceOutcome(
        mahasiswa_id=mahasiswa_id,
        nama="",
        nim="",
        status="tercatat",
        message="Absensi berhasil dicatat.",
        similarity=similarity,
        recorded_at=timestamp,
        session_id=session_id,
        det_status=det_status,
        n_wajah=n_wajah,
        bbox=bbox,
    )


def _bbox_text(bbox: Optional[list]) -> Optional[str]:
    if not bbox:
        return None
    try:
        return ",".join(str(round(float(v), 1)) for v in bbox)
    except Exception:
        return None


def clear_cooldowns() -> None:
    with _cooldown_lock:
        _identity_cooldown.clear()
