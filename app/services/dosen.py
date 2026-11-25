"""Service dosen (data dosen & akun login dosen)."""

from __future__ import annotations

from typing import Any, Optional

from app.db.engine import db, now_iso
from app.core import security


def list_dosen(q: str = "", page: int = 1, per_page: int = 25) -> dict:
    where, params = [], []
    if q:
        where.append("(lower(d.nama) LIKE ? OR lower(COALESCE(d.email,'')) LIKE ? "
                     "OR lower(COALESCE(d.nid,'')) LIKE ?)")
        params.extend([f"%{q.lower()}%"] * 3)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(db.query_value(f"SELECT COUNT(*) FROM dosen d{clause}", params, 0))
    offset = max(0, (max(1, page) - 1) * per_page)
    rows = db.query_all(
        f"""SELECT d.id, d.user_id, d.nid, d.nama, d.email, d.no_hp, d.aktif,
                   u.username, u.role,
                   (SELECT COUNT(*) FROM kelas_dosen kd WHERE kd.dosen_id = d.id) AS n_kelas
            FROM dosen d LEFT JOIN users u ON u.id = d.user_id{clause}
            ORDER BY d.nama LIMIT ? OFFSET ?""",
        params + [per_page, offset],
    )
    return {"rows": rows, "total": total, "page": max(1, page), "per_page": per_page}


def get_dosen(dosen_id: int) -> Optional[dict]:
    return db.query_one(
        """SELECT d.*, u.username, u.aktif AS user_aktif FROM dosen d
           LEFT JOIN users u ON u.id = d.user_id WHERE d.id = ?""",
        (dosen_id,),
    )


def create_dosen(
    nama: str,
    email: Optional[str] = None,
    nid: Optional[str] = None,
    no_hp: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    buat_akun: bool = True,
) -> tuple[int, Optional[int]]:
    """Buat data dosen; opsional sekaligus membuat akun login (role=dosen)."""
    ts = now_iso()
    user_id: Optional[int] = None
    if buat_akun:
        if not username or not password:
            raise ValueError("username dan kata sandi wajib untuk membuat akun dosen")
        user_id = db.execute(
            """INSERT INTO users (username, password_hash, role, nama, email, nim, aktif,
                                 created_at, updated_at)
               VALUES (?, ?, 'dosen', ?, ?, ?, 1, ?, ?)""",
            (username.strip().lower(), security.hash_password(password), nama.strip(),
             email or None, nid or None, ts, ts),
        )
    dosen_id = db.execute(
        """INSERT INTO dosen (user_id, nid, nama, email, no_hp, aktif, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
        (user_id, nid or None, nama.strip(), email or None, no_hp or None, ts, ts),
    )
    return dosen_id, user_id


def update_dosen(dosen_id: int, **fields: Any) -> None:
    allowed = {"nama", "email", "nid", "no_hp", "aktif"}
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
    params.append(dosen_id)
    db.execute(f"UPDATE dosen SET {', '.join(sets)} WHERE id = ?", params)


def delete_dosen(dosen_id: int) -> None:
    row = get_dosen(dosen_id)
    db.execute("DELETE FROM dosen WHERE id = ?", (dosen_id,))
    if row and row.get("user_id"):
        db.execute("DELETE FROM users WHERE id = ?", (row["user_id"],))


def kelas_dosen(dosen_id: int) -> list[dict]:
    return db.query_all(
        """SELECT k.id, k.kode_mk, k.nama_mk, k.label, k.status
           FROM kelas_dosen kd JOIN kelas k ON k.id = kd.class_id
           WHERE kd.dosen_id = ? ORDER BY k.label""",
        (dosen_id,),
    )


def set_class_dosen(class_id: int, dosen_id: int, aktif: bool = True) -> bool:
    if aktif:
        existing = db.query_one(
            "SELECT id FROM kelas_dosen WHERE class_id = ? AND dosen_id = ?",
            (class_id, dosen_id),
        )
        if existing:
            return False
        db.execute(
            "INSERT INTO kelas_dosen (class_id, dosen_id, created_at) VALUES (?, ?, ?)",
            (class_id, dosen_id, now_iso()),
        )
        return True
    db.execute(
        "DELETE FROM kelas_dosen WHERE class_id = ? AND dosen_id = ?",
        (class_id, dosen_id),
    )
    return True


def dosen_user_id(dosen_id: int) -> Optional[int]:
    row = db.query_one("SELECT user_id FROM dosen WHERE id = ?", (dosen_id,))
    return int(row["user_id"]) if row and row.get("user_id") else None


def find_dosen_by_user(user_id: int) -> Optional[dict]:
    return db.query_one("SELECT * FROM dosen WHERE user_id = ?", (user_id,))


def list_pengampu(dosen_id: int) -> list[dict]:
    """Kelas yang diampu dosen (dosen_id pada kelas ATAU tabel relasi)."""
    return db.query_all(
        """SELECT DISTINCT k.id, k.kode_mk, k.nama_mk, k.label, k.status
           FROM kelas k
           LEFT JOIN kelas_dosen kd ON kd.class_id = k.id
           WHERE k.dosen_id = ? OR kd.dosen_id = ?
           ORDER BY k.label""",
        (dosen_id, dosen_id),
    )
