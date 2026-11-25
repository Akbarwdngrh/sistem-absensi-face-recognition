"""Seed data demo fiktif untuk portofolio / pengujian cepat.

Membuat 1 kelas contoh (`DEMO-A`), 2 mahasiswa placeholder fiktif (tanpa foto
asli maupun data biometrik), dan 1 sesi absensi yang langsung terbuka agar
penguji dapat mencoba alur aplikasi dengan mudah.

Jalankan:
    python -m scripts.seed_demo
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.engine import audit, db, init_db  # noqa: E402
from app.services import kelas as svc_kelas  # noqa: E402
from app.services import mahasiswa as svc_mahasiswa  # noqa: E402

DEMO_CLASS_CODE = "DEMO101"
DEMO_CLASS_NAME = "Pengenalan Wajah (Demo)"
DEMO_CLASS_LABEL = "DEMO-A"

DEMO_STUDENTS = [
    ("20260001", "Pengguna Demo 1"),
    ("20260002", "Pengguna Demo 2"),
]


def main() -> int:
    """Isi data demo secara idempoten tanpa menyentuh artefak riset."""
    init_db()

    row = db.query_one(
        "SELECT id FROM kelas WHERE lower(kode_mk)=lower(?) AND lower(label)=lower(?)",
        (DEMO_CLASS_CODE, DEMO_CLASS_LABEL),
    )
    if row:
        class_id = int(row["id"])
        created_class = False
    else:
        class_id = svc_kelas.create_kelas(
            kode_mk=DEMO_CLASS_CODE,
            nama_mk=DEMO_CLASS_NAME,
            label=DEMO_CLASS_LABEL,
            status="aktif",
        )
        created_class = True

    added_students = 0
    added_members = 0
    for nim, nama in DEMO_STUDENTS:
        mhs = svc_mahasiswa.find_by_nim(nim)
        if mhs is None:
            mhs_id = svc_mahasiswa.create_mahasiswa(nim=nim, nama=nama)
            added_students += 1
        else:
            mhs_id = int(mhs["id"])
        if svc_mahasiswa.set_class_membership(class_id, mhs_id, True):
            added_members += 1

    today = datetime.now().strftime("%Y-%m-%d")
    sesi = db.query_one(
        "SELECT id, status FROM sesi_absensi WHERE class_id = ? AND status = 'buka' LIMIT 1",
        (class_id,),
    )
    if sesi is None:
        session_id = svc_kelas.create_sesi(
            class_id=class_id,
            tanggal=today,
            mulai_jam="08:00",
            keterangan="Sesi demo portofolio",
        )
        created_session = True
    else:
        session_id = int(sesi["id"])
        created_session = False

    audit(
        None,
        "seed_demo",
        "database",
        class_id,
        f"kelas_baru={created_class} mhs_baru={added_students} sesi={session_id}",
    )

    print("=== Data demo siap ===")
    print(f"  Kelas            : {DEMO_CLASS_CODE} / {DEMO_CLASS_LABEL} (id={class_id})")
    print(f"  Mahasiswa demo   : {len(DEMO_STUDENTS)} entri fiktif (+{added_students} baru, +{added_members} anggota)")
    print(f"  Sesi absensi buka: id={session_id} ({'baru dibuat' if created_session else 'sudah ada'})")
    print("\nLangkah mencoba:")
    print("  1. Jalankan server lalu buka /setup untuk membuat akun Admin pertama.")
    print("  2. Login sebagai Admin -> tab Mahasiswa -> klik 'Daftarkan wajah' pada 'Pengguna Demo 1'")
    print("     lalu unggah foto wajah Anda sendiri (embedding disimpan lokal di SQLite).")
    print("  3. Buka /absen, pilih sesi DEMO-A, nyalakan kamera, dan uji pengenalan wajah.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
