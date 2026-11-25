"""Service kelas & sesi absensi."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.db.engine import db, now_iso


# ---------------------------------------------------------------------------
# Kelas
# ---------------------------------------------------------------------------
def list_kelas(
    q: str = "",
    dosen_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 25,
    status: Optional[str] = None,
) -> dict:
    where, params = [], []
    if q:
        where.append("(lower(k.label) LIKE ? OR lower(k.nama_mk) LIKE ? OR lower(k.kode_mk) LIKE ?)")
        params.extend([f"%{q.lower()}%"] * 3)
    if dosen_id:
        where.append("(k.dosen_id = ? OR EXISTS (SELECT 1 FROM kelas_dosen kd "
                     "WHERE kd.class_id = k.id AND kd.dosen_id = ?))")
        params.extend([dosen_id, dosen_id])
    if status:
        where.append("k.status = ?")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(db.query_value(f"SELECT COUNT(*) FROM kelas k{clause}", params, 0))
    offset = max(0, (max(1, page) - 1) * per_page)
    rows = db.query_all(
        f"""SELECT k.id, k.kode_mk, k.nama_mk, k.label, k.dosen_id, k.tahun_akademik,
                   k.semester, k.status,
                   d.nama AS dosen_nama,
                   (SELECT COUNT(*) FROM kelas_mahasiswa km WHERE km.class_id = k.id) AS n_mhs,
                   (SELECT COUNT(*) FROM sesi_absensi s WHERE s.class_id = k.id) AS n_sesi
            FROM kelas k LEFT JOIN dosen d ON d.id = k.dosen_id{clause}
            ORDER BY k.label LIMIT ? OFFSET ?""",
        params + [per_page, offset],
    )
    return {"rows": rows, "total": total, "page": max(1, page), "per_page": per_page}


def get_kelas(class_id: int) -> Optional[dict]:
    return db.query_one(
        """SELECT k.*, d.nama AS dosen_nama, d.email AS dosen_email
           FROM kelas k LEFT JOIN dosen d ON d.id = k.dosen_id WHERE k.id = ?""",
        (class_id,),
    )


def create_kelas(
    kode_mk: str,
    nama_mk: str,
    label: str,
    dosen_id: Optional[int] = None,
    tahun_akademik: Optional[str] = None,
    semester: Optional[str] = None,
    status: str = "aktif",
) -> int:
    ts = now_iso()
    class_id = db.execute(
        """INSERT INTO kelas (kode_mk, nama_mk, label, dosen_id, tahun_akademik, semester,
                              status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (kode_mk.strip(), nama_mk.strip(), label.strip(), dosen_id,
         tahun_akademik or None, semester or None, status, ts, ts),
    )
    if dosen_id:
        db.execute(
            "INSERT INTO kelas_dosen (class_id, dosen_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT (class_id, dosen_id) DO NOTHING",
            (class_id, dosen_id, ts),
        )
    return class_id


def update_kelas(class_id: int, **fields: Any) -> None:
    allowed = {"kode_mk", "nama_mk", "label", "dosen_id", "tahun_akademik", "semester", "status"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(now_iso())
    params.append(class_id)
    db.execute(f"UPDATE kelas SET {', '.join(sets)} WHERE id = ?", params)


def delete_kelas(class_id: int) -> None:
    db.execute("DELETE FROM kelas WHERE id = ?", (class_id,))


# ---------------------------------------------------------------------------
# Sesi absensi
# ---------------------------------------------------------------------------
def list_sesi(class_id: Optional[int] = None, status: Optional[str] = None,
              limit: int = 100) -> list[dict]:
    where, params = [], []
    if class_id:
        where.append("s.class_id = ?")
        params.append(class_id)
    if status:
        where.append("s.status = ?")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return db.query_all(
        f"""SELECT s.id, s.class_id, s.tanggal, s.mulai_at, s.selesai_at, s.status,
                   s.keterangan, s.created_at,
                   k.label, k.nama_mk, k.kode_mk,
                   (SELECT COUNT(*) FROM kehadiran h WHERE h.session_id = s.id) AS n_hadir,
                   (SELECT COUNT(*) FROM kelas_mahasiswa km WHERE km.class_id = s.class_id) AS n_anggota
            FROM sesi_absensi s JOIN kelas k ON k.id = s.class_id{clause}
            ORDER BY s.tanggal DESC, s.mulai_at DESC LIMIT ?""",
        params + [limit],
    )


def sesi_publik_aktif(limit: int = 50) -> list[dict]:
    """Sesi yang sedang dibuka — untuk halaman publik mahasiswa.

    TIDAK memuat data mahasiswa; hanya informasi kelas & sesi.
    """
    return db.query_all(
        """SELECT s.id, s.tanggal, s.mulai_at, s.selesai_at, s.keterangan,
                  k.label, k.nama_mk, k.kode_mk, k.tahun_akademik, k.semester,
                  (SELECT COUNT(*) FROM kelas_mahasiswa km WHERE km.class_id = k.id) AS n_anggota
           FROM sesi_absensi s JOIN kelas k ON k.id = s.class_id
           WHERE s.status = 'buka' AND k.status = 'aktif'
           ORDER BY s.mulai_at DESC LIMIT ?""",
        (limit,),
    )


def get_sesi(session_id: int) -> Optional[dict]:
    return db.query_one(
        """SELECT s.*, k.label, k.nama_mk, k.kode_mk, k.status AS class_status
           FROM sesi_absensi s JOIN kelas k ON k.id = s.class_id WHERE s.id = ?""",
        (session_id,),
    )


def create_sesi(
    class_id: int,
    tanggal: str,
    mulai_jam: str,
    selesai_jam: Optional[str] = None,
    keterangan: Optional[str] = None,
    created_by: Optional[int] = None,
) -> int:
    ts = now_iso()
    mulai_at = f"{tanggal}T{mulai_jam}:00"
    selesai_at = f"{tanggal}T{selesai_jam}:00" if selesai_jam else None
    return db.execute(
        """INSERT INTO sesi_absensi (class_id, tanggal, mulai_at, selesai_at, status,
                                    keterangan, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'buka', ?, ?, ?, ?)""",
        (class_id, tanggal, mulai_at, selesai_at, keterangan or None, created_by, ts, ts),
    )


def close_sesi(session_id: int) -> None:
    ts = now_iso()
    db.execute(
        "UPDATE sesi_absensi SET status = 'tutup', selesai_at = COALESCE(selesai_at, ?), "
        "updated_at = ? WHERE id = ?",
        (ts, ts, session_id),
    )


def reopen_sesi(session_id: int) -> None:
    ts = now_iso()
    db.execute(
        "UPDATE sesi_absensi SET status = 'buka', updated_at = ? WHERE id = ?",
        (ts, session_id),
    )


def valid_tanggal(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False
