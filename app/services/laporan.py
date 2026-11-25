"""Service laporan: rekap absensi + ekspor CSV/XLSX.

Ekspor selalu mengikuti **hak akses** pemanggil: dosen hanya boleh mengekspor
kelas yang diampu. Sel yang diawali `= + - @` diberi prefix `'` (formula
injection) â€” sama seperti V1.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from app import config
from app.db.engine import db

KOLOM_LAPORAN = [
    "No",
    "Tanggal",
    "Waktu",
    "NIM",
    "Nama Mahasiswa",
    "Kelas",
    "Mata Kuliah",
    "Sesi",
    "Status Kehadiran",
    "Skor Kemiripan",
    "Metode",
    "Sumber",
    "Catatan",
]

_INJECTION_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value and value[0] in _INJECTION_PREFIX:
        return "'" + value
    return value


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "export").strip("_")
    return cleaned or "export"


# ---------------------------------------------------------------------------
# Query rekap
# ---------------------------------------------------------------------------
def rekap(
    class_id: Optional[int] = None,
    session_id: Optional[int] = None,
    tanggal: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    scope: Optional[dict] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Ambil catatan kehadiran sesuai cakupan akses.

    ``scope`` = {"dosen_id": int} untuk membatasi ke kelas yang diampu.
    """
    where: list[str] = []
    params: list[Any] = []
    if class_id:
        where.append("h.class_id = ?")
        params.append(class_id)
    if session_id:
        where.append("h.session_id = ?")
        params.append(session_id)
    if tanggal:
        where.append("substr(h.recorded_at, 1, 10) = ?")
        params.append(tanggal)
    if status:
        where.append("h.status = ?")
        params.append(status)
    if q:
        where.append("(lower(m.nama) LIKE ? OR lower(m.nim) LIKE ?)")
        params.extend([f"%{q.lower()}%", f"%{q.lower()}%"])
    if scope and scope.get("dosen_id"):
        where.append(
            "(h.class_id IN (SELECT id FROM kelas WHERE dosen_id = ?) OR "
            "h.class_id IN (SELECT class_id FROM kelas_dosen WHERE dosen_id = ?))"
        )
        params.extend([scope["dosen_id"], scope["dosen_id"]])
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = int(db.query_value(
        f"""SELECT COUNT(*) FROM kehadiran h
            JOIN mahasiswa m ON m.id = h.mahasiswa_id{clause}""",
        params, 0))
    sql = f"""SELECT h.id, h.recorded_at, h.status, h.similarity, h.metode, h.sumber,
                    h.det_status, h.n_wajah, h.catatan, h.class_id, h.session_id,
                    h.mahasiswa_id, h.created_by,
                    m.nim, m.nama,
                    k.label AS kelas, k.nama_mk, k.kode_mk,
                    s.tanggal, s.id AS session_ref
             FROM kehadiran h
             JOIN mahasiswa m ON m.id = h.mahasiswa_id
             JOIN kelas k ON k.id = h.class_id
             JOIN sesi_absensi s ON s.id = h.session_id{clause}
             ORDER BY h.recorded_at DESC"""
    params2 = list(params)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params2 += [limit, offset]
    rows = db.query_all(sql, params2)
    return rows, total


def rekap_sesi(session_id: int, scope: Optional[dict] = None) -> list[dict]:
    rows, _ = rekap(session_id=session_id, scope=scope)
    return rows


def statistik(class_id: Optional[int] = None, scope: Optional[dict] = None) -> dict:
    rows, total = rekap(class_id=class_id, scope=scope)
    hadir = sum(1 for r in rows if r.get("status") == "HADIR")
    manual = sum(1 for r in rows if r.get("metode") == "manual")
    kelas_ids = {r.get("class_id") for r in rows if r.get("class_id")}
    return {
        "total_catatan": total,
        "hadir": hadir,
        "manual": manual,
        "kelas_terlibat": len(kelas_ids),
    }


# ---------------------------------------------------------------------------
# Ekspor
# ---------------------------------------------------------------------------
def _rows_to_export(records: Iterable[dict]) -> list[dict]:
    out = []
    for i, r in enumerate(records, start=1):
        recorded_at = str(r.get("recorded_at") or "")
        tanggal = recorded_at[:10] if len(recorded_at) >= 10 else ""
        waktu = recorded_at[11:19] if len(recorded_at) >= 19 else recorded_at
        skor = r.get("similarity")
        out.append({
            "No": i,
            "Tanggal": sanitize_cell(tanggal),
            "Waktu": sanitize_cell(waktu),
            "NIM": sanitize_cell(str(r.get("nim") or "")),
            "Nama Mahasiswa": sanitize_cell(str(r.get("nama") or "")),
            "Kelas": sanitize_cell(str(r.get("kelas") or "")),
            "Mata Kuliah": sanitize_cell(str(r.get("nama_mk") or "")),
            "Sesi": sanitize_cell(str(r.get("tanggal") or "")),
            "Status Kehadiran": sanitize_cell(str(r.get("status") or "")),
            "Skor Kemiripan": ("{:.4f}".format(float(skor)) if skor is not None else ""),
            "Metode": sanitize_cell(str(r.get("metode") or "")),
            "Sumber": sanitize_cell(str(r.get("sumber") or "")),
            "Catatan": sanitize_cell(str(r.get("catatan") or "")),
        })
    return out


def export_records(records: list[dict], fmt: str = "csv", prefix: str = "absensi") -> Path:
    """Tulis file ekspor ke EXPORT_DIR V2. Mengembalikan path file."""
    config.ensure_dirs()
    rows = _rows_to_export(records)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = "{}_{}".format(sanitize_filename(prefix), stamp)
    fmt = (fmt or "csv").lower()

    if fmt in ("xlsx", "excel"):
        path = config.EXPORT_DIR / (base + ".xlsx")
        import pandas as pd

        df = pd.DataFrame(rows, columns=KOLOM_LAPORAN)
        df.to_excel(path, index=False, engine="openpyxl")
        return path

    path = config.EXPORT_DIR / (base + ".csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=KOLOM_LAPORAN)
        writer.writeheader()
        writer.writerows(rows)
    return path

