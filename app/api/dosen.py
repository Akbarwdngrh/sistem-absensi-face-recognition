"""API dosen â€” akses dibatasi pada kelas yang diampu.

Cakupan yang sama berlaku untuk dashboard, rekap, dan ekspor. Dosen TIDAK
dapat melihat atau memodifikasi kelas lain.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request
from fastapi.responses import FileResponse

from app import config
from app.core import deps, security
from app.db.engine import audit, db, now_iso
from app.schemas import KoreksiHadirIn, SesiIn
from app.services import dosen as svc_dosen
from app.services import kelas as svc_kelas
from app.services import laporan as svc_laporan


def dosen_guard(request: Request) -> dict:
    """Gerbang tingkat router: staff wajib login; CSRF wajib untuk mutasi."""
    user = deps.require_roles(request, "admin", "dosen")
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token") or ""
        if not security.csrf_matches(deps.get_secret(), user["session_token"], token):
            raise HTTPException(status_code=403, detail="Token CSRF tidak valid")
    return user


router = APIRouter(prefix="/api/dosen", tags=["dosen"])
# Gerbang tingkat router: dijalankan SEBELUM validasi body.
router.dependencies.append(Depends(dosen_guard))


def dosen_context(request: Request) -> tuple[dict, Optional[int]]:
    """Return (user, dosen_id). Dosen_id = None bila akun tidak tertaut data dosen."""
    user = deps.require_roles(request, "admin", "dosen")
    if user["role"] == "admin":
        return user, None
    row = svc_dosen.find_dosen_by_user(int(user["id"]))
    return user, (int(row["id"]) if row else None)


def scope_for(dosen_id: Optional[int]) -> Optional[dict]:
    if dosen_id is None:
        return None  # admin: seluruh data
    return {"dosen_id": dosen_id}


def ensure_class_access(dosen_id: Optional[int], class_id: int) -> None:
    """Dosen hanya boleh mengakses kelas yang diampu."""
    if dosen_id is None:
        return
    row = db.query_one(
        """SELECT 1 AS ok FROM kelas k
           LEFT JOIN kelas_dosen kd ON kd.class_id = k.id
           WHERE k.id = ? AND (k.dosen_id = ? OR kd.dosen_id = ?)""",
        (class_id, dosen_id, dosen_id),
    )
    if row is None:
        raise HTTPException(status_code=403, detail="Kelas di luar kewenangan Anda.")


@router.get("/ringkasan")
def ringkasan(request: Request):
    user, dosen_id = dosen_context(request)
    scope = scope_for(dosen_id)
    kelas = svc_dosen.list_pengampu(dosen_id) if dosen_id is not None else []
    sesi = svc_kelas.list_sesi(status="buka")
    if scope:
        allowed = {k["id"] for k in kelas}
        sesi = [s for s in sesi if s["class_id"] in allowed]
    rows, total = svc_laporan.rekap(scope=scope)
    hadir = sum(1 for r in rows if r.get("status") == "HADIR")
    return {
        "n_kelas": len(kelas),
        "kelas": kelas,
        "sesi_buka": sesi,
        "total_catatan": total,
        "hadir": hadir,
    }


@router.get("/kelas/{class_id}/kehadiran")
def kelas_kehadiran(request: Request, class_id: int, page: int = 1, per_page: int = 50):
    user, dosen_id = dosen_context(request)
    ensure_class_access(dosen_id, class_id)
    rows, total = svc_laporan.rekap(class_id=class_id, scope=scope_for(dosen_id))
    offset = max(0, (max(1, page) - 1) * per_page)
    return {"rows": rows[offset: offset + per_page], "total": total,
            "page": max(1, page), "per_page": per_page}


@router.get("/kelas/{class_id}/daftar")
def kelas_daftar(request: Request, class_id: int):
    """Daftar mahasiswa kelas + status kehadiran terakhir."""
    user, dosen_id = dosen_context(request)
    ensure_class_access(dosen_id, class_id)
    anggota = db.query_all(
        """SELECT m.id, m.nim, m.nama, m.email,
                  (SELECT COUNT(*) FROM kehadiran h
                    WHERE h.mahasiswa_id = m.id AND h.class_id = ? AND h.status='HADIR') AS n_hadir,
                  (SELECT MAX(h.recorded_at) FROM kehadiran h
                    WHERE h.mahasiswa_id = m.id AND h.class_id = ? AND h.status='HADIR') AS terakhir
           FROM kelas_mahasiswa km JOIN mahasiswa m ON m.id = km.mahasiswa_id
           WHERE km.class_id = ? ORDER BY m.nama""",
        (class_id, class_id, class_id),
    )
    return {"rows": anggota, "total": len(anggota)}


@router.get("/kehadiran")
def kehadiran(request: Request, class_id: Optional[int] = None, session_id: Optional[int] = None,
              tanggal: Optional[str] = None, status: Optional[str] = None,
              q: Optional[str] = None, page: int = 1, per_page: int = 25):
    user, dosen_id = dosen_context(request)
    if class_id:
        ensure_class_access(dosen_id, class_id)
    rows, total = svc_laporan.rekap(class_id=class_id, session_id=session_id, tanggal=tanggal,
                                    status=status, q=q, scope=scope_for(dosen_id))
    offset = max(0, (max(1, page) - 1) * per_page)
    return {"rows": rows[offset: offset + per_page], "total": total,
            "page": max(1, page), "per_page": per_page}


@router.put("/kehadiran/{record_id}")
def koreksi(request: Request, record_id: int, payload: KoreksiHadirIn):
    """Koreksi absensi oleh dosen (hanya kelas sendiri) â€” wajib audit trail."""
    user, dosen_id = dosen_context(request)
    row = db.query_one("SELECT * FROM kehadiran WHERE id = ?", (record_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Catatan kehadiran tidak ditemukan.")
    ensure_class_access(dosen_id, int(row["class_id"]))
    before = {"status": row["status"], "metode": row["metode"], "catatan": row.get("catatan")}
    db.execute(
        """UPDATE kehadiran SET status = ?, metode = 'manual', catatan = ?, updated_at = ?
           WHERE id = ?""",
        (payload.status, payload.catatan, now_iso(), record_id),
    )
    audit(user["id"], "kehadiran_koreksi_dosen", "kehadiran", record_id,
          f"sebelum={before}; sesudah=status={payload.status}; "
          f"alasan={payload.catatan or '-'}", ip=deps.client_ip(request))
    return {"ok": True, "before": before}


@router.post("/sesi")
def buat_sesi(request: Request, payload: SesiIn):
    """Dosen boleh membuka sesi absensi untuk kelas yang diampu."""
    user, dosen_id = dosen_context(request)
    ensure_class_access(dosen_id, payload.class_id)
    try:
        session_id = svc_kelas.create_sesi(
            class_id=payload.class_id, tanggal=payload.tanggal,
            mulai_jam=payload.mulai_jam, selesai_jam=payload.selesai_jam,
            keterangan=payload.keterangan, created_by=user["id"],
        )
    except Exception:
        raise HTTPException(status_code=409, detail="Sesi dengan waktu yang sama sudah ada.")
    audit(user["id"], "sesi_create_dosen", "sesi_absensi", session_id,
          f"class={payload.class_id}", ip=deps.client_ip(request))
    return {"ok": True, "id": session_id}


@router.post("/sesi/{session_id}/tutup")
def tutup_sesi(request: Request, session_id: int):
    user, dosen_id = dosen_context(request)
    sesi = svc_kelas.get_sesi(session_id)
    if not sesi:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan.")
    ensure_class_access(dosen_id, int(sesi["class_id"]))
    svc_kelas.close_sesi(session_id)
    audit(user["id"], "sesi_tutup_dosen", "sesi_absensi", session_id,
          ip=deps.client_ip(request))
    return {"ok": True}


@router.get("/export")
def export(request: Request, fmt: str = "csv", class_id: Optional[int] = None,
           session_id: Optional[int] = None, tanggal: Optional[str] = None,
           status: Optional[str] = None, q: Optional[str] = None):
    """Unduh laporan kelas yang diampu saja."""
    user, dosen_id = dosen_context(request)
    if class_id:
        ensure_class_access(dosen_id, class_id)
    rows, _ = svc_laporan.rekap(class_id=class_id, session_id=session_id, tanggal=tanggal,
                                status=status, q=q, scope=scope_for(dosen_id))
    if not rows:
        raise HTTPException(status_code=404, detail="Tidak ada data untuk diekspor.")
    path = svc_laporan.export_records(rows, fmt=fmt, prefix="absensi_dosen")
    audit(user["id"], "export_dosen", "kehadiran", None, f"{len(rows)} baris, {fmt}",
          ip=deps.client_ip(request))
    media = ("text/csv" if fmt == "csv"
             else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return FileResponse(str(path), media_type=media, filename=path.name)

