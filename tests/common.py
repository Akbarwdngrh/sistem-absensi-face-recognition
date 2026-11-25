"""Bootstrap pengujian V2.

Set environment SEBELUM `app.config` diimpor sehingga setiap modul tes
menggunakan database sementara — TIDAK menyentuh database V2 yang aktif
maupun database V1.

Aturan keras: tidak ada tes yang boleh menulis ke folder V1 atau folder model.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.gettempdir()) / "absensi_v2_test"
_TMP.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("SECRET_KEY", "kunci-uji-v2-jangan-dipakai-di-produksi-000000")
os.environ["DATABASE_PATH"] = str(_TMP / "absensi_v2_test.db")
os.environ["EXPORT_DIR"] = str(_TMP / "exports")
os.environ["LOG_DIR"] = str(_TMP / "logs")
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["LOAD_ENGINE_ON_STARTUP"] = "0"
os.environ["FACE_DEVICE"] = "cpu"
os.environ["PUBLIC_ATTENDANCE_ENABLED"] = "true"
os.environ["ALLOW_FACE_ENROLLMENT"] = "true"
# Matikan throttle inferensi saat pengujian.
os.environ["INFER_MIN_INTERVAL_MS"] = "0"
os.environ["IDENTITY_COOLDOWN_SECONDS"] = "0"


def db_path() -> Path:
    return Path(os.environ["DATABASE_PATH"])


def fresh_db() -> None:
    """Hapus database uji lalu buat ulang."""
    path = db_path()
    for suffix in ("", "-wal", "-shm"):
        extra = Path(str(path) + suffix)
        if extra.exists():
            extra.unlink()
    from app.db.engine import init_db

    init_db()


def make_admin(username: str = "admin", password: str = "AdminRahasia123",
               nama: str = "Admin Uji") -> int:
    from app.core import security
    from app.db.engine import db, now_iso

    ts = now_iso()
    return db.execute(
        """INSERT INTO users (username, password_hash, role, nama, email, nim, aktif,
                             created_at, updated_at)
           VALUES (?, ?, 'admin', ?, NULL, NULL, 1, ?, ?)""",
        (username, security.hash_password(password), nama, ts, ts),
    )


def make_dosen(username: str = "dosen", password: str = "DosenRahasia123",
               nama: str = "Dosen Uji") -> tuple[int, int]:
    from app.core import security
    from app.db.engine import db, now_iso

    ts = now_iso()
    user_id = db.execute(
        """INSERT INTO users (username, password_hash, role, nama, email, nim, aktif,
                             created_at, updated_at)
           VALUES (?, ?, 'dosen', ?, 'dosen@uji.local', NULL, 1, ?, ?)""",
        (username, security.hash_password(password), nama, ts, ts),
    )
    dosen_id = db.execute(
        """INSERT INTO dosen (user_id, nid, nama, email, no_hp, aktif, created_at, updated_at)
           VALUES (?, ?, ?, ?, NULL, 1, ?, ?)""",
        (user_id, "NID001", nama, "dosen@uji.local", ts, ts),
    )
    return dosen_id, user_id


def make_mahasiswa(nim: str, nama: str, person: str = "") -> int:
    from app.db.engine import db, now_iso

    ts = now_iso()
    mhs_id = db.execute(
        """INSERT INTO mahasiswa (nim, nama, email, no_hp, aktif, dibuat_oleh,
                                 created_at, updated_at)
           VALUES (?, ?, NULL, NULL, 1, NULL, ?, ?)""",
        (nim, nama, ts, ts),
    )
    if person:
        db.execute(
            """INSERT INTO face_enrollments
               (mahasiswa_id, person_label, sumber, embedding, dimensi, aktif, catatan,
                created_at, updated_at)
               VALUES (?, ?, 'gallery', NULL, NULL, 1, NULL, ?, ?)""",
            (mhs_id, person, ts, ts),
        )
    return mhs_id


def make_kelas(label: str = "SK7A", kode: str = "SK7A", nama_mk: str = "Kelas Uji",
               dosen_id: int | None = None) -> int:
    from app.db.engine import db, now_iso

    ts = now_iso()
    class_id = db.execute(
        """INSERT INTO kelas (kode_mk, nama_mk, label, dosen_id, tahun_akademik, semester,
                              status, created_at, updated_at)
           VALUES (?, ?, ?, ?, NULL, NULL, 'aktif', ?, ?)""",
        (kode, nama_mk, label, dosen_id, ts, ts),
    )
    if dosen_id:
        db.execute(
            "INSERT INTO kelas_dosen (class_id, dosen_id, created_at) VALUES (?, ?, ?)",
            (class_id, dosen_id, ts),
        )
    return class_id


def make_sesi(class_id: int, tanggal: str = "2026-01-01", mulai: str = "08:00",
              status: str = "buka", created_by: int | None = None) -> int:
    from app.db.engine import db, now_iso

    ts = now_iso()
    return db.execute(
        """INSERT INTO sesi_absensi (class_id, tanggal, mulai_at, selesai_at, status,
                                    keterangan, created_by, created_at, updated_at)
           VALUES (?, ?, ?, NULL, ?, NULL, ?, ?, ?)""",
        (class_id, tanggal, f"{tanggal}T{mulai}:00", status, created_by, ts, ts),
    )


def add_member(class_id: int, mahasiswa_id: int) -> None:
    from app.db.engine import db, now_iso

    db.execute(
        "INSERT INTO kelas_mahasiswa (class_id, mahasiswa_id, created_at) VALUES (?, ?, ?)",
        (class_id, mahasiswa_id, now_iso()),
    )
