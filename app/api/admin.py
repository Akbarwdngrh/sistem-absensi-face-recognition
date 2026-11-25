"""API admin â€” seluruhnya memerlukan role `admin` dan CSRF untuk mutasi.

Setiap perubahan data Sensitive (koreksi absensi, hapus akun, ekspor) menulis
jejak ke `audit_log`.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app import config
from app.core import deps, security
from app.db.engine import audit, db, now_iso
from app.face import pipeline
from app.face.engine import engine
from app.schemas import (
    DosenIn,
    DosenUpdate,
    KelasIn,
    KelasUpdate,
    KoreksiHadirIn,
    KoreksiManualIn,
    MahasiswaIn,
    MahasiswaUpdate,
    PasswordIn,
    SesiIn,
)
from app.services import dosen as svc_dosen
from app.services import kelas as svc_kelas
from app.services import laporan as svc_laporan
from app.services import mahasiswa as svc_mahasiswa

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Dependency: admin + CSRF untuk method mutasi
# ---------------------------------------------------------------------------
def admin_user(request: Request) -> dict:
    return deps.require_admin(request)


def admin_guard(request: Request) -> dict:
    """Gerbang tingkat router.

    Dijalankan SEBELUM validasi body, sehingga request tanpa login / tanpa
    token CSRF ditolak lebih dulu (bukan berupa 422 validasi bentuk data).
    """
    user = deps.require_admin(request)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token") or ""
        if not security.csrf_matches(deps.get_secret(), user["session_token"], token):
            raise HTTPException(status_code=403, detail="Token CSRF tidak valid")
    return user


def admin_write(request: Request) -> dict:
    user = deps.require_admin(request)
    token = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token") or ""
    if not deps.current_user(request):
        raise HTTPException(status_code=401, detail="Belum masuk")
    if not security.csrf_matches(deps.get_secret(), deps.current_user(request)["session_token"], token):
        raise HTTPException(status_code=403, detail="Token CSRF tidak valid")
    return user


# Gerbang tingkat router: dijalankan SEBELUM validasi body sehingga request
# tanpa login / tanpa token CSRF ditolak lebih dulu (bukan 422 validasi data).
router.dependencies.append(Depends(admin_guard))


# ---------------------------------------------------------------------------
# Statistik
# ---------------------------------------------------------------------------
@router.get("/stats")
def stats(request: Request):
    admin_user(request)
    mhs = svc_mahasiswa.statistik_mahasiswa()
    kelas_total = int(db.query_value("SELECT COUNT(*) FROM kelas", (), 0))
    kelas_aktif = int(db.query_value("SELECT COUNT(*) FROM kelas WHERE status='aktif'", (), 0))
    sesi_buka = int(db.query_value(
        "SELECT COUNT(*) FROM sesi_absensi s JOIN kelas k ON k.id=s.class_id "
        "WHERE s.status='buka' AND k.status='aktif'", (), 0))
    hadir_total = int(db.query_value(
        "SELECT COUNT(*) FROM kehadiran WHERE status='HADIR'", (), 0))
    manual_total = int(db.query_value(
        "SELECT COUNT(*) FROM kehadiran WHERE metode='manual'", (), 0))
    engine_status = engine.status
    return {
        "mahasiswa": mhs,
        "kelas": {"total": kelas_total, "aktif": kelas_aktif},
        "sesi_buka": sesi_buka,
        "kehadiran": {"hadir": hadir_total, "manual": manual_total},
        "engine": {
            "ready": engine_status.loaded,
            "device": engine_status.device,
            "persons": engine_status.n_persons,
            "threshold": engine_status.threshold,
            "error": engine_status.error,
        },
    }


# ---------------------------------------------------------------------------
# Mahasiswa
# ---------------------------------------------------------------------------
@router.get("/mahasiswa")
def mahasiswa_list(
    request: Request,
    q: str = "",
    class_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 25,
):
    admin_user(request)
    return svc_mahasiswa.list_mahasiswa(q=q, class_id=class_id, page=page, per_page=per_page)


@router.post("/mahasiswa")
def mahasiswa_create(request: Request, payload: MahasiswaIn):
    user = admin_write(request)
    if svc_mahasiswa.find_by_nim(payload.nim):
        raise HTTPException(status_code=409, detail="NIM sudah terdaftar.")
    new_id = svc_mahasiswa.create_mahasiswa(
        nim=payload.nim, nama=payload.nama, email=payload.email,
        no_hp=payload.no_hp, dibuat_oleh=user["id"],
    )
    audit(user["id"], "mahasiswa_create", "mahasiswa", new_id, f"nim={payload.nim}",
          ip=deps.client_ip(request))
    return {"ok": True, "id": new_id}


@router.get("/mahasiswa/{mahasiswa_id}")
def mahasiswa_detail(request: Request, mahasiswa_id: int):
    admin_user(request)
    mhs = svc_mahasiswa.get_mahasiswa(mahasiswa_id)
    if not mhs:
        raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan.")
    return {
        "mahasiswa": mhs,
        "kelas": svc_mahasiswa.kelas_mahasiswa(mahasiswa_id),
        "wajah": db.query_all(
            "SELECT id, person_label, sumber, dimensi, aktif, catatan, created_at "
            "FROM face_enrollments WHERE mahasiswa_id = ? ORDER BY id",
            (mahasiswa_id,),
        ),
    }


@router.put("/mahasiswa/{mahasiswa_id}")
def mahasiswa_update(request: Request, mahasiswa_id: int, payload: MahasiswaUpdate):
    user = admin_write(request)
    if not svc_mahasiswa.get_mahasiswa(mahasiswa_id):
        raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan.")
    fields = payload.model_dump(exclude_none=True)
    if "nim" in fields:
        existing = svc_mahasiswa.find_by_nim(fields["nim"])
        if existing and int(existing["id"]) != mahasiswa_id:
            raise HTTPException(status_code=409, detail="NIM sudah digunakan mahasiswa lain.")
    svc_mahasiswa.update_mahasiswa(mahasiswa_id, **fields)
    audit(user["id"], "mahasiswa_update", "mahasiswa", mahasiswa_id,
          ",".join(sorted(fields.keys())), ip=deps.client_ip(request))
    return {"ok": True}


@router.delete("/mahasiswa/{mahasiswa_id}")
def mahasiswa_delete(request: Request, mahasiswa_id: int):
    user = admin_write(request)
    mhs = svc_mahasiswa.get_mahasiswa(mahasiswa_id)
    if not mhs:
        raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan.")
    svc_mahasiswa.delete_mahasiswa(mahasiswa_id)
    audit(user["id"], "mahasiswa_delete", "mahasiswa", mahasiswa_id,
          f"nim={mhs['nim']}", ip=deps.client_ip(request))
    return {"ok": True}


@router.post("/mahasiswa/{mahasiswa_id}/kelas")
def mahasiswa_kelas(request: Request, mahasiswa_id: int, class_id: int = Form(...),
                    aktif: bool = Form(True)):
    user = admin_write(request)
    if not svc_mahasiswa.get_mahasiswa(mahasiswa_id):
        raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan.")
    if not svc_kelas.get_kelas(class_id):
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan.")
    changed = svc_mahasiswa.set_class_membership(class_id, mahasiswa_id, aktif)
    audit(user["id"], "anggota_kelas", "mahasiswa", mahasiswa_id,
          f"class={class_id} aktif={aktif}", ip=deps.client_ip(request))
    return {"ok": True, "changed": changed}


# ---------------------------------------------------------------------------
# Enrollment wajah (admin)
# ---------------------------------------------------------------------------
@router.post("/mahasiswa/{mahasiswa_id}/wajah")
async def enroll_wajah(
    request: Request,
    mahasiswa_id: int,
    image: UploadFile = File(...),
    catatan: str = Form(""),
):
    """Daftarkan wajah baru untuk mahasiswa.

    Gambar diproses dengan pipeline kanonik yang sama dengan galeri, lalu
    embedding disimpan pada DB V2 (tabel `face_enrollments`). Artefak model
    dan galeri riset TIDAK pernah ditulis.
    """
    user = admin_write(request)
    if not config.ALLOW_FACE_ENROLLMENT:
        raise HTTPException(status_code=403, detail="Enrollment wajah dinonaktifkan.")
    mhs = svc_mahasiswa.get_mahasiswa(mahasiswa_id)
    if not mhs:
        raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan.")

    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="Gambar kosong.")
    if len(data) > config.MAX_FRAME_BYTES:
        raise HTTPException(status_code=413, detail="Ukuran gambar terlalu besar.")
    bgr = pipeline.imread_bytes(data)
    if bgr is None:
        raise HTTPException(status_code=400, detail="Format gambar tidak dikenali.")
    try:
        embedding = engine.embed_single(bgr)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Mesin pengenalan wajah belum siap.")
    if embedding is None:
        raise HTTPException(status_code=422, detail="Wajah tidak terdeteksi pada gambar.")

    person = f"v2_{mhs['nim']}_{now_iso().replace(':', '').replace('-', '')}"
    ts = now_iso()
    db.execute(
        """INSERT INTO face_enrollments
           (mahasiswa_id, person_label, sumber, embedding, dimensi, aktif, catatan,
            created_at, updated_at)
           VALUES (?, ?, 'upload', ?, ?, 1, ?, ?, ?)""",
        (mahasiswa_id, person, embedding.astype("float32").tobytes(), int(embedding.shape[0]),
         catatan or None, ts, ts),
    )
    # Daftarkan ke galeri aktif (memori proses V2 saja).
    engine.register_embedding(person, mhs["nama"], mhs["nim"], "", embedding)
    audit(user["id"], "face_enroll", "face_enrollments", mahasiswa_id,
          f"person={person}", ip=deps.client_ip(request))
    return {"ok": True, "person_label": person, "dimensi": int(embedding.shape[0])}


@router.delete("/wajah/{enrollment_id}")
def hapus_wajah(request: Request, enrollment_id: int):
    user = admin_write(request)
    row = db.query_one("SELECT * FROM face_enrollments WHERE id = ?", (enrollment_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Data wajah tidak ditemukan.")
    db.execute("DELETE FROM face_enrollments WHERE id = ?", (enrollment_id,))
    audit(user["id"], "face_enroll_delete", "face_enrollments", enrollment_id,
          f"person={row['person_label']}", ip=deps.client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Dosen
# ---------------------------------------------------------------------------
@router.get("/dosen")
def dosen_list(request: Request, q: str = "", page: int = 1, per_page: int = 25):
    admin_user(request)
    return svc_dosen.list_dosen(q=q, page=page, per_page=per_page)


@router.post("/dosen")
def dosen_create(request: Request, payload: DosenIn):
    user = admin_write(request)
    if payload.buat_akun:
        if not payload.username or not payload.password:
            raise HTTPException(status_code=422,
                                detail="Username dan kata sandi wajib untuk membuat akun.")
        err = security.validate_password(payload.password, config.PASSWORD_MIN_LENGTH,
                                         username=payload.username or "",
                                         nama=payload.nama)
        if err:
            raise HTTPException(status_code=422, detail=err)
    try:
        dosen_id, user_id = svc_dosen.create_dosen(
            nama=payload.nama, email=payload.email, nid=payload.nid, no_hp=payload.no_hp,
            username=payload.username, password=payload.password, buat_akun=payload.buat_akun,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Gagal menyimpan dosen: {exc}")
    audit(user["id"], "dosen_create", "dosen", dosen_id,
          f"username={payload.username or '-'}", ip=deps.client_ip(request))
    return {"ok": True, "id": dosen_id, "user_id": user_id}


@router.put("/dosen/{dosen_id}")
def dosen_update(request: Request, dosen_id: int, payload: DosenUpdate):
    user = admin_write(request)
    if not svc_dosen.get_dosen(dosen_id):
        raise HTTPException(status_code=404, detail="Dosen tidak ditemukan.")
    fields = payload.model_dump(exclude_none=True)
    svc_dosen.update_dosen(dosen_id, **fields)
    audit(user["id"], "dosen_update", "dosen", dosen_id,
          ",".join(sorted(fields.keys())), ip=deps.client_ip(request))
    return {"ok": True}


@router.delete("/dosen/{dosen_id}")
def dosen_delete(request: Request, dosen_id: int):
    user = admin_write(request)
    row = svc_dosen.get_dosen(dosen_id)
    if not row:
        raise HTTPException(status_code=404, detail="Dosen tidak ditemukan.")
    svc_dosen.delete_dosen(dosen_id)
    audit(user["id"], "dosen_delete", "dosen", dosen_id, f"nama={row['nama']}",
          ip=deps.client_ip(request))
    return {"ok": True}


@router.post("/dosen/{dosen_id}/password")
def dosen_password(request: Request, dosen_id: int, payload: PasswordIn):
    """Setel ulang kata sandi akun dosen (akun dihentikan)."""
    user = admin_write(request)
    row = svc_dosen.get_dosen(dosen_id)
    if not row or not row.get("user_id"):
        raise HTTPException(status_code=404, detail="Akun dosen tidak ditemukan.")
    err = security.validate_password(payload.password, config.PASSWORD_MIN_LENGTH,
                                     username=row.get("username") or "", nama=row.get("nama") or "")
    if err:
        raise HTTPException(status_code=422, detail=err)
    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (security.hash_password(payload.password), now_iso(), row["user_id"]),
        )
        cur.execute("DELETE FROM auth_sessions WHERE user_id = ?", (row["user_id"],))
        conn.commit()
    audit(user["id"], "password_reset", "users", row["user_id"],
          f"oleh admin untuk dosen {row['nama']}", ip=deps.client_ip(request))
    return {"ok": True}


@router.post("/dosen/{dosen_id}/kelas")
def dosen_kelas(request: Request, dosen_id: int, class_id: int = Form(...),
                aktif: bool = Form(True)):
    user = admin_write(request)
    if not svc_dosen.get_dosen(dosen_id):
        raise HTTPException(status_code=404, detail="Dosen tidak ditemukan.")
    if not svc_kelas.get_kelas(class_id):
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan.")
    changed = svc_dosen.set_class_dosen(class_id, dosen_id, aktif)
    audit(user["id"], "dosen_kelas", "dosen", dosen_id,
          f"class={class_id} aktif={aktif}", ip=deps.client_ip(request))
    return {"ok": True, "changed": changed}


# ---------------------------------------------------------------------------
# Kelas
# ---------------------------------------------------------------------------
@router.get("/kelas")
def kelas_list(request: Request, q: str = "", page: int = 1, per_page: int = 25,
               status: Optional[str] = None):
    admin_user(request)
    return svc_kelas.list_kelas(q=q, page=page, per_page=per_page, status=status)


@router.post("/kelas")
def kelas_create(request: Request, payload: KelasIn):
    user = admin_write(request)
    exists = db.query_one(
        "SELECT id FROM kelas WHERE lower(kode_mk)=lower(?) AND lower(label)=lower(?)",
        (payload.kode_mk, payload.label),
    )
    if exists:
        raise HTTPException(status_code=409, detail="Kelas dengan kode & label sama sudah ada.")
    class_id = svc_kelas.create_kelas(
        kode_mk=payload.kode_mk, nama_mk=payload.nama_mk, label=payload.label,
        dosen_id=payload.dosen_id, tahun_akademik=payload.tahun_akademik,
        semester=payload.semester, status=payload.status,
    )
    audit(user["id"], "kelas_create", "kelas", class_id,
          f"{payload.kode_mk}/{payload.label}", ip=deps.client_ip(request))
    return {"ok": True, "id": class_id}


@router.get("/kelas/{class_id}")
def kelas_detail(request: Request, class_id: int):
    admin_user(request)
    kelas = svc_kelas.get_kelas(class_id)
    if not kelas:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan.")
    anggota = db.query_all(
        """SELECT m.id, m.nim, m.nama, m.email
           FROM kelas_mahasiswa km JOIN mahasiswa m ON m.id = km.mahasiswa_id
           WHERE km.class_id = ? ORDER BY m.nama""",
        (class_id,),
    )
    return {"kelas": kelas, "anggota": anggota}


@router.put("/kelas/{class_id}")
def kelas_update(request: Request, class_id: int, payload: KelasUpdate):
    user = admin_write(request)
    if not svc_kelas.get_kelas(class_id):
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan.")
    fields = payload.model_dump(exclude_none=True)
    svc_kelas.update_kelas(class_id, **fields)
    audit(user["id"], "kelas_update", "kelas", class_id,
          ",".join(sorted(fields.keys())), ip=deps.client_ip(request))
    return {"ok": True}


@router.delete("/kelas/{class_id}")
def kelas_delete(request: Request, class_id: int):
    user = admin_write(request)
    kelas = svc_kelas.get_kelas(class_id)
    if not kelas:
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan.")
    svc_kelas.delete_kelas(class_id)
    audit(user["id"], "kelas_delete", "kelas", class_id,
          f"{kelas['kode_mk']}/{kelas['label']}", ip=deps.client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sesi absensi
# ---------------------------------------------------------------------------
@router.get("/sesi")
def sesi_list(request: Request, class_id: Optional[int] = None,
              status: Optional[str] = None):
    admin_user(request)
    return {"rows": svc_kelas.list_sesi(class_id=class_id, status=status)}


@router.post("/sesi")
def sesi_create(request: Request, payload: SesiIn):
    user = admin_write(request)
    if not svc_kelas.get_kelas(payload.class_id):
        raise HTTPException(status_code=404, detail="Kelas tidak ditemukan.")
    try:
        session_id = svc_kelas.create_sesi(
            class_id=payload.class_id, tanggal=payload.tanggal,
            mulai_jam=payload.mulai_jam, selesai_jam=payload.selesai_jam,
            keterangan=payload.keterangan, created_by=user["id"],
        )
    except Exception:
        raise HTTPException(status_code=409, detail="Sesi dengan waktu yang sama sudah ada.")
    audit(user["id"], "sesi_create", "sesi_absensi", session_id,
          f"class={payload.class_id}", ip=deps.client_ip(request))
    return {"ok": True, "id": session_id}


@router.post("/sesi/{session_id}/tutup")
def sesi_tutup(request: Request, session_id: int):
    user = admin_write(request)
    if not svc_kelas.get_sesi(session_id):
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan.")
    svc_kelas.close_sesi(session_id)
    audit(user["id"], "sesi_tutup", "sesi_absensi", session_id, ip=deps.client_ip(request))
    return {"ok": True}


@router.post("/sesi/{session_id}/buka")
def sesi_buka(request: Request, session_id: int):
    user = admin_write(request)
    if not svc_kelas.get_sesi(session_id):
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan.")
    svc_kelas.reopen_sesi(session_id)
    audit(user["id"], "sesi_buka", "sesi_absensi", session_id, ip=deps.client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Kehadiran & koreksi (WAJIB audit trail)
# ---------------------------------------------------------------------------
@router.get("/kehadiran")
def kehadiran_list(
    request: Request,
    class_id: Optional[int] = None,
    session_id: Optional[int] = None,
    tanggal: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 25,
):
    admin_user(request)
    rows, total = svc_laporan.rekap(class_id=class_id, session_id=session_id, tanggal=tanggal,
                                    status=status, q=q)
    offset = max(0, (max(1, page) - 1) * per_page)
    return {
        "rows": rows[offset: offset + per_page],
        "total": total,
        "page": max(1, page),
        "per_page": per_page,
    }


@router.put("/kehadiran/{record_id}")
def kehadiran_koreksi(request: Request, record_id: int, payload: KoreksiHadirIn):
    """Koreksi manual terhadap catatan kehadiran. WAJIB meninggalkan jejak audit."""
    user = admin_write(request)
    row = db.query_one("SELECT * FROM kehadiran WHERE id = ?", (record_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Catatan kehadiran tidak ditemukan.")
    before = {"status": row["status"], "metode": row["metode"], "catatan": row.get("catatan")}
    db.execute(
        """UPDATE kehadiran SET status = ?, metode = 'manual', catatan = ?, updated_at = ?
           WHERE id = ?""",
        (payload.status, payload.catatan, now_iso(), record_id),
    )
    audit(
        user["id"], "kehadiran_koreksi", "kehadiran", record_id,
        f"sebelum={before}; sesudah=status={payload.status}; alasan={payload.catatan or '-'}",
        ip=deps.client_ip(request),
    )
    return {"ok": True, "before": before}


@router.post("/kehadiran/manual")
def kehadiran_manual(request: Request, payload: KoreksiManualIn):
    """Catat kehadiran manual (mis. mahasiswa tanpa wajah terdaftar)."""
    user = admin_write(request)
    session = svc_kelas.get_sesi(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan.")
    mhs = svc_mahasiswa.get_mahasiswa(payload.mahasiswa_id)
    if not mhs:
        raise HTTPException(status_code=404, detail="Mahasiswa tidak ditemukan.")
    ts = now_iso()
    db.execute(
        """INSERT INTO kehadiran
           (session_id, class_id, mahasiswa_id, status, similarity, metode, sumber,
            catatan, recorded_at, created_by, updated_at)
           VALUES (?, ?, ?, ?, NULL, 'manual', 'manual', ?, ?, ?, ?)
           ON CONFLICT (session_id, mahasiswa_id) DO NOTHING""",
        (payload.session_id, session["class_id"], payload.mahasiswa_id, payload.status,
         payload.catatan, ts, user["id"], ts),
    )
    audit(user["id"], "kehadiran_manual", "kehadiran", payload.mahasiswa_id,
          f"session={payload.session_id}; status={payload.status}; "
          f"alasan={payload.catatan or '-'}", ip=deps.client_ip(request))
    return {"ok": True}


@router.delete("/kehadiran/{record_id}")
def kehadiran_hapus(request: Request, record_id: int, alasan: str = Form(...)):
    user = admin_write(request)
    row = db.query_one("SELECT * FROM kehadiran WHERE id = ?", (record_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Catatan kehadiran tidak ditemukan.")
    db.execute("DELETE FROM kehadiran WHERE id = ?", (record_id,))
    audit(user["id"], "kehadiran_hapus", "kehadiran", record_id,
          f"alasan={alasan}", ip=deps.client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
@router.get("/audit")
def audit_list(request: Request, page: int = 1, per_page: int = 50,
               aksi: Optional[str] = None, user_id: Optional[int] = None):
    admin_user(request)
    where, params = [], []
    if aksi:
        where.append("a.aksi LIKE ?")
        params.append(f"%{aksi}%")
    if user_id:
        where.append("a.user_id = ?")
        params.append(user_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(db.query_value(f"SELECT COUNT(*) FROM audit_log a{clause}", params, 0))
    offset = max(0, (max(1, page) - 1) * per_page)
    rows = db.query_all(
        f"""SELECT a.id, a.aksi, a.entitas, a.entitas_id, a.detail, a.ip, a.created_at,
                   u.username, u.nama
            FROM audit_log a LEFT JOIN users u ON u.id = a.user_id{clause}
            ORDER BY a.id DESC LIMIT ? OFFSET ?""",
        params + [per_page, offset],
    )
    return {"rows": rows, "total": total, "page": max(1, page), "per_page": per_page}


# ---------------------------------------------------------------------------
# Ekspor
# ---------------------------------------------------------------------------
@router.get("/export")
def export(request: Request, fmt: str = "csv", class_id: Optional[int] = None,
           session_id: Optional[int] = None, tanggal: Optional[str] = None,
           status: Optional[str] = None, q: Optional[str] = None):
    user = admin_user(request)
    rows, _ = svc_laporan.rekap(class_id=class_id, session_id=session_id, tanggal=tanggal,
                                status=status, q=q)
    if not rows:
        raise HTTPException(status_code=404, detail="Tidak ada data untuk diekspor.")
    path = svc_laporan.export_records(rows, fmt=fmt, prefix="absensi_v2")
    audit(user["id"], "export", "kehadiran", None, f"{len(rows)} baris, {fmt}",
          ip=deps.client_ip(request))
    media = ("text/csv" if fmt == "csv"
             else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return FileResponse(str(path), media_type=media, filename=path.name)

