"""Seed data akademik V2 dari roster biometrik (READ-ONLY).

Membaca ``gallery_meta.json`` (folder riset) lalu membuat:
  * data mahasiswa (NIM + nama + person_label sebagai enrollment wajah)
  * kelas (label kelas yang muncul pada roster)
  * keanggotaan kelas

TIDAK membuat akun login (mahasiswa tidak login pada V2) dan TIDAK membuat
kata sandi default apa pun. Admin & dosen dibuat lewat dashboard admin.

Jalankan:
    .\\.venv\\Scripts\\python.exe -m scripts.seed_from_roster
    .\\.venv\\Scripts\\python.exe -m scripts.seed_from_roster --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.db.engine import audit, db, init_db, now_iso  # noqa: E402
from app.services import kelas as svc_kelas  # noqa: E402
from app.services import mahasiswa as svc_mahasiswa  # noqa: E402

# Pemetaan label kelas dari metadata ke label final.
LABEL_MERGE = {
    "SKPROF": "SKU7A",
    "SKPROFESIONAL": "SKU7A",
    "SKPROFESSIONAL": "SKU7A",
    "SKALP": "SKU7A",
    "SKALIH": "SKU7A",
    "SKALIHPROGRAM": "SKU7A",
    "P1": "SKU7A",
}

NAMA_KELAS = {
    "SK7A": ("SK7A", "Kapita Selekta Kelas 7A"),
    "SKU7A": ("SKU7A", "Kapita Selekta Kelas Unggulan 7A"),
}


def canonical_label(label: str) -> str:
    key = (label or "").strip().upper()
    return LABEL_MERGE.get(key, key)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s or "siswa"


def load_people() -> list[dict]:
    path = Path(config.GALLERY_META)
    if not path.exists():
        raise FileNotFoundError(f"gallery_meta.json tidak ditemukan: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    out = []
    for item in raw.get("orang", []):
        person = str(item.get("person") or "").strip()
        nama = str(item.get("nama") or "").strip()
        nim = str(item.get("nim") or "").strip()
        label = canonical_label(str(item.get("kelas") or ""))
        if not nim:
            # 13 entri riset tidak punya NIM; gunakan slug nama sebagai identitas lokal.
            nim = "X" + slug(person or nama)[:16].upper()
        if not nama:
            nama = person or nim
        out.append({
            "person": person,
            "nama": nama,
            "nim": nim,
            "kelas": label,
            "aktif": bool(item.get("aktif", True)),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed data akademik V2 dari roster biometrik")
    ap.add_argument("--dry-run", action="store_true", help="Tampilkan rencana tanpa menulis.")
    args = ap.parse_args(argv)

    init_db()
    people = load_people()
    print(f"Roster terbaca: {len(people)} orang")

    kelas_labels = sorted({p["kelas"] for p in people if p["kelas"] and p["kelas"] != "-"})
    if args.dry_run:
        print("Kelas yang akan dibuat:", ", ".join(kelas_labels))
        print("Mahasiswa yang akan dibuat:", len(people))
        print("(dry-run: tidak ada data yang ditulis)")
        return 0

    created_mhs = created_kelas = created_anggota = 0
    class_ids: dict[str, int] = {}

    for label in kelas_labels:
        kode, nama_mk = NAMA_KELAS.get(label, (label, f"Kapita Selekta {label}"))
        row = db.query_one(
            "SELECT id FROM kelas WHERE lower(kode_mk)=lower(?) AND lower(label)=lower(?)",
            (kode, label),
        )
        if row:
            class_ids[label] = int(row["id"])
        else:
            class_ids[label] = svc_kelas.create_kelas(
                kode_mk=kode, nama_mk=nama_mk, label=label, status="aktif"
            )
            created_kelas += 1

    ts = now_iso()
    for p in people:
        row = svc_mahasiswa.find_by_nim(p["nim"])
        if row is None:
            mhs_id = svc_mahasiswa.create_mahasiswa(nim=p["nim"], nama=p["nama"])
            created_mhs += 1
        else:
            mhs_id = int(row["id"])
        # Enrollment biometrik:aitama gallery (embeddings tetap di artefak riset).
        if p["person"]:
            exists = db.query_one(
                "SELECT id FROM face_enrollments WHERE mahasiswa_id = ? AND person_label = ?",
                (mhs_id, p["person"]),
            )
            if not exists:
                db.execute(
                    """INSERT INTO face_enrollments
                       (mahasiswa_id, person_label, sumber, embedding, dimensi, aktif, catatan,
                        created_at, updated_at)
                       VALUES (?, ?, 'gallery', NULL, NULL, 1, ?, ?, ?)""",
                    (mhs_id, p["person"], "dari gallery_meta.json", ts, ts),
                )
        label = p["kelas"]
        if label and label in class_ids:
            if svc_mahasiswa.set_class_membership(class_ids[label], mhs_id, True):
                created_anggota += 1

    audit(None, "seed_roster", "database", None,
          f"mahasiswa={created_mhs} kelas={created_kelas} anggota={created_anggota}")

    print("=== Seeding selesai ===")
    print(f"  mahasiswa dibuat : {created_mhs}")
    print(f"  kelas dibuat     : {created_kelas}")
    print(f"  keanggotaan      : {created_anggota}")
    print("\nCatatan: mahasiswa TIDAK memiliki akun login pada V2 (absensi via kamera).")
    print("Buat akun admin & dosen lewat dashboard admin atau /setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

