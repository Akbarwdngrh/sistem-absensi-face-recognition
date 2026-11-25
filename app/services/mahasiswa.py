"""Service mahasiswa (data induk mahasiswa, tanpa akun login)."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.db.engine import db, now_iso

NIM_RE = re.compile(r"^\d{6,}$")


def valid_nim(nim: str) -> bool:
    return bool(nim) and NIM_RE.match(nim.strip()) is not None


def list_mahasiswa(
    q: str = "",
    class_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 25,
    aktif: Optional[int] = None,
) -> dict:
    where: list[str] = []
    params: list[Any] = []
    if q:
        where.append("(lower(m.nama) LIKE ? OR lower(m.nim) LIKE ?)")
        params.extend([f"%{q.lower()}%", f"%{q.lower()}%"])
    if aktif is not None:
        where.append("m.aktif = ?")
        params.append(aktif)
    if class_id:
        where.append("EXISTS (SELECT 1 FROM kelas_mahasiswa km "
                     "WHERE km.mahasiswa_id = m.id AND km.class_id = ?)")
        params.append(class_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = int(db.query_value(f"SELECT COUNT(*) FROM mahasiswa m{clause}", params, 0))
    offset = max(0, (max(1, page) - 1) * per_page)
    rows = db.query_all(
        f"""SELECT m.id, m.nim, m.nama, m.email, m.no_hp, m.aktif, m.created_at,
                   (SELECT COUNT(*) FROM kelas_mahasiswa km WHERE km.mahasiswa_id = m.id) AS n_kelas,
                   (SELECT COUNT(*) FROM face_enrollments fe
                     WHERE fe.mahasiswa_id = m.id AND fe.aktif = 1) AS n_wajah
            FROM mahasiswa m{clause}
            ORDER BY m.nama LIMIT ? OFFSET ?""",
        params + [per_page, offset],
    )
    return {"rows": rows, "total": total, "page": max(1, page), "per_page": per_page}


def get_mahasiswa(mahasiswa_id: int) -> Optional[dict]:
    return db.query_one("SELECT * FROM mahasiswa WHERE id = ?", (mahasiswa_id,))


def find_by_nim(nim: str) -> Optional[dict]:
    return db.query_one("SELECT * FROM mahasiswa WHERE lower(nim) = lower(?)", (nim.strip(),))


def find_by_person_label(person: str) -> Optional[dict]:
    """Mahasiswa yang memiliki enrollment biometrik dengan person_label tertentu."""
    if not person:
        return None
    row = db.query_one(
        """SELECT m.* FROM mahasiswa m
           JOIN face_enrollments fe ON fe.mahasiswa_id = m.id
           WHERE fe.person_label = ? AND fe.aktif = 1
           ORDER BY m.id LIMIT 1""",
        (person,),
    )
    return row


def create_mahasiswa(
    nim: str,
    nama: str,
    email: Optional[str] = None,
    no_hp: Optional[str] = None,
    dibuat_oleh: Optional[int] = None,
) -> int:
    ts = now_iso()
    return db.execute(
        """INSERT INTO mahasiswa (nim, nama, email, no_hp, aktif, dibuat_oleh, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
        (nim.strip(), nama.strip(), email or None, no_hp or None, dibuat_oleh, ts, ts),
    )


def update_mahasiswa(mahasiswa_id: int, **fields: Any) -> None:
    allowed = {"nim", "nama", "email", "no_hp", "aktif"}
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
    params.append(mahasiswa_id)
    db.execute(f"UPDATE mahasiswa SET {', '.join(sets)} WHERE id = ?", params)


def delete_mahasiswa(mahasiswa_id: int) -> None:
    db.execute("DELETE FROM mahasiswa WHERE id = ?", (mahasiswa_id,))


def kelas_mahasiswa(mahasiswa_id: int) -> list[dict]:
    return db.query_all(
        """SELECT k.id, k.kode_mk, k.nama_mk, k.label, k.status
           FROM kelas_mahasiswa km JOIN kelas k ON k.id = km.class_id
           WHERE km.mahasiswa_id = ? ORDER BY k.label""",
        (mahasiswa_id,),
    )


def set_class_membership(class_id: int, mahasiswa_id: int, aktif: bool = True) -> bool:
    """Tambah/lepas anggota kelas. Mengembalikan True bila berubah."""
    if aktif:
        existing = db.query_one(
            "SELECT id FROM kelas_mahasiswa WHERE class_id = ? AND mahasiswa_id = ?",
            (class_id, mahasiswa_id),
        )
        if existing:
            return False
        db.execute(
            "INSERT INTO kelas_mahasiswa (class_id, mahasiswa_id, created_at) VALUES (?, ?, ?)",
            (class_id, mahasiswa_id, now_iso()),
        )
        return True
    db.execute(
        "DELETE FROM kelas_mahasiswa WHERE class_id = ? AND mahasiswa_id = ?",
        (class_id, mahasiswa_id),
    )
    return True


def statistik_mahasiswa() -> dict:
    total = int(db.query_value("SELECT COUNT(*) FROM mahasiswa", (), 0))
    aktif = int(db.query_value("SELECT COUNT(*) FROM mahasiswa WHERE aktif = 1", (), 0))
    dengan_wajah = int(db.query_value(
        """SELECT COUNT(DISTINCT mahasiswa_id) FROM face_enrollments WHERE aktif = 1""", (), 0))
    return {"total": total, "aktif": aktif, "dengan_wajah": dengan_wajah}
