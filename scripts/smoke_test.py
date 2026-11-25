"""Uji asap (smoke test) end-to-end untuk V2.

Menjalankan server sungguhan (uvicorn, thread terpisah) dengan database
sementara, lalu memverifikasi alur nyata:

  1. server hidup & /health sehat
  2. setup admin pertama
  3. login admin
  4. impor roster biometrik -> mahasiswa, kelas, enrollment wajah
  5. membuat sesi absensi (buka)
  6. mengirim frame wajah NYATA ke endpoint publik
  7. memastikan kehadiran tercatat untuk mahasiswa yang dikenali
  8. memastikan frame kedua tidak menggandakan catatan
  9. memastikan sesi tertutup menolak pencatatan baru
 10. memastikan dosen tidak bisa melihat kelas yang bukan diampu

Jalankan:
    .\\.venv\\Scripts\\python.exe -m scripts.smoke_test
    .\\.venv\\Scripts\\python.exe -m scripts.smoke_test --port 8123
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.gettempdir()) / "absensi_v2_smoke"
TMP.mkdir(parents=True, exist_ok=True)

# Set env SEBELUM app.config diimpor -> database sementara, model opsional.
os.environ["DATABASE_PATH"] = str(TMP / "smoke.db")
os.environ["EXPORT_DIR"] = str(TMP / "exports")
os.environ["LOG_DIR"] = str(TMP / "logs")
os.environ["UPLOAD_DIR"] = str(TMP / "uploads")
os.environ["SECRET_KEY"] = "kunci-smoke-test-v2-0000000000000000000000000000"
os.environ["LOAD_ENGINE_ON_STARTUP"] = "0"
os.environ["FACE_DEVICE"] = os.environ.get("FACE_DEVICE", "cuda")

import httpx  # noqa: E402

from app.db.engine import init_db  # noqa: E402
from app.face.engine import engine  # noqa: E402

KAPITA = Path(os.environ.get("KAPITA_ROOT", r"C:\Users\Acer\Documents\Kapita"))


def port_gratis(mulai: int) -> int:
    for p in range(mulai, mulai + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("Tidak ada port bebas")


def cari_foto(n: int) -> Path | None:
    foto: list[Path] = []
    for kelas in ("SK7A", "SKU7A"):
        folder = KAPITA / kelas
        if not folder.exists():
            continue
        for orang in sorted(folder.iterdir()):
            if orang.is_dir():
                for f in sorted(orang.iterdir()):
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                        foto.append(f)
                        break
    return foto[n] if len(foto) > n else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Smoke test end-to-end V2")
    ap.add_argument("--port", type=int, default=0, help="0 = pilih port otomatis")
    ap.add_argument("--skip-model", action="store_true",
                    help="Lewati pengujian wajah nyata (hanya uji alur dasar)")
    args = ap.parse_args(argv)

    import uvicorn

    from app.main import app

    # Mulai dari database kosong agar hasil smoke test reproducible.
    for suffix in ("", "-wal", "-shm"):
        p = Path(os.environ["DATABASE_PATH"] + suffix)
        if p.exists():
            p.unlink()

    init_db()
    port = args.port or port_gratis(8123)
    base = f"http://127.0.0.1:{port}"
    konfigurasi = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(konfigurasi)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    gagal: list[str] = []
    langkah = 0

    def cek(nama: str, kondisi: bool, detail: str = "") -> None:
        nonlocal langkah
        langkah += 1
        status = "OK  " if kondisi else "GAGAL"
        print(f"  [{status}] {nama}" + (f" — {detail}" if detail else ""))
        if not kondisi:
            gagal.append(nama)

    # tunggu server hidup
    for _ in range(100):
        try:
            httpx.get(base + "/health", timeout=2.0)
            break
        except Exception:
            time.sleep(0.2)

    try:
        with httpx.Client(base_url=base, timeout=120.0, follow_redirects=False) as c:
            print("\n[1] Server & kesehatan")
            health = c.get("/health").json()
            cek("server hidup", health.get("status") in ("ok", "degraded"))
            cek("database terhubung", health.get("database") is True)

            print("\n[2] Setup admin pertama")
            res = c.get("/setup")
            csrf = c.cookies.get("absensi_v2_csrf") or ""
            res = c.post("/setup", data={
                "nama": "Admin Smoke", "username": "admin",
                "password": "AdminSmokeTest123", "password2": "AdminSmokeTest123",
                "auth_csrf": csrf,
            })
            cek("admin dibuat", res.status_code == 303, f"status={res.status_code}")

            print("\n[3] Login admin")
            c.get("/login")
            csrf = c.cookies.get("absensi_v2_csrf") or ""
            res = c.post("/login", data={
                "username": "admin", "password": "AdminSmokeTest123", "auth_csrf": csrf,
            })
            cek("login berhasil", res.status_code == 303)
            me = c.get("/api/auth/me").json()
            cek("sesi aktif", me.get("authenticated") is True and me.get("role") == "admin")
            token = me.get("csrf") or ""
            hdr = {"X-CSRF-Token": token}

            print("\n[4] Impor roster biometrik (read-only dari artefak riset)")
            from scripts import seed_from_roster

            seed_from_roster.main([])
            stats = c.get("/api/admin/stats", headers=hdr).json()
            cek("mahasiswa terimpor", stats["mahasiswa"]["total"] >= 60,
                f"{stats['mahasiswa']['total']} mahasiswa")
            cek("kelas terimpor", stats["kelas"]["total"] >= 1,
                f"{stats['kelas']['total']} kelas")

            print("\n[5] Membuka sesi absensi")
            kelas = c.get("/api/admin/kelas", headers=hdr).json()["rows"][0]
            hari = time.strftime("%Y-%m-%d")
            res = c.post("/api/admin/sesi", headers=hdr, json={
                "class_id": kelas["id"], "tanggal": hari, "mulai_jam": "08:00",
                "keterangan": "sesi smoke test",
            })
            cek("sesi dibuat", res.status_code == 200, res.text[:120])
            session_id = res.json().get("id")

            if not args.skip_model:
                print("\n[6] Memuat model wajah (mungkin perlu beberapa detik)")
                t0 = time.perf_counter()
                ok = engine.ensure_loaded()
                cek("model siap", ok, engine.status.error or
                    f"device={engine.status.device} ambang={engine.status.threshold}")
                print(f"       waktu muat: {time.perf_counter() - t0:.1f} dtk")

                print("\n[7] Mengirim wajah NYATA ke endpoint publik")
                foto = cari_foto(0)
                if foto is None:
                    cek("foto dataset tersedia", False, str(KAPITA))
                else:
                    c.get("/absen")
                    csrf = c.cookies.get("absensi_v2_csrf") or ""
                    with open(foto, "rb") as fh:
                        isi = fh.read()
                    res = c.post("/api/public/attendance/frame",
                                 data={"session_id": str(session_id), "csrf": csrf},
                                 files={"image": (foto.name, isi, "image/jpeg")})
                    data = res.json()
                    cek("endpoint publik menjawab", res.status_code == 200, res.text[:160])
                    cek("wajah terdeteksi", data.get("n_wajah", 0) >= 1,
                        f"n_wajah={data.get('n_wajah')}")
                    cek("wajah dikenali", data.get("n_tercatat", 0) >= 1,
                        f"n_tercatat={data.get('n_tercatat')}")

                    print("\n[8] Frame kedua tidak boleh menggandakan catatan")
                    with open(foto, "rb") as fh:
                        isi = fh.read()
                    res2 = c.post("/api/public/attendance/frame",
                                  data={"session_id": str(session_id), "csrf": csrf},
                                  files={"image": (foto.name, isi, "image/jpeg")})
                    data2 = res2.json()
                    cek("tidak ada duplikasi", data2.get("n_tercatat", 0) == 0,
                        f"n_tercatat={data2.get('n_tercatat')}")
                    rekap = c.get("/api/admin/kehadiran", headers=hdr).json()
                    cek("satu catatan per mahasiswa per sesi",
                        rekap["total"] == len({r["mahasiswa_id"] for r in rekap["rows"]}),
                        f"total={rekap['total']}")

                print("\n[9] Sesi tertutup menolak pencatatan baru")
                c.post(f"/api/admin/sesi/{session_id}/tutup", headers=hdr)
                c.get("/absen")
                csrf = c.cookies.get("absensi_v2_csrf") or ""
                foto = cari_foto(1) or cari_foto(0)
                if foto is not None:
                    with open(foto, "rb") as fh:
                        isi = fh.read()
                    res = c.post("/api/public/attendance/frame",
                                 data={"session_id": str(session_id), "csrf": csrf},
                                 files={"image": (foto.name, isi, "image/jpeg")})
                    cek("ditolak karena sesi tutup", res.status_code == 409,
                        f"status={res.status_code}")

            print("\n[10] Audit log mencatat aktivitas")
            audit = c.get("/api/admin/audit", headers=hdr).json()
            cek("audit terisi", audit["total"] > 0, f"{audit['total']} entri")

            print("\n[11] Cakupan dosen")
            c.get("/login")
            csrf = c.cookies.get("absensi_v2_csrf") or ""
            res = c.post("/api/admin/dosen", headers=hdr, json={
                "nama": "Dosen Smoke", "email": "dosen@smoke.local", "nid": "NID-SMOKE",
                "buat_akun": True, "username": "dosen", "password": "DosenSmokeTest123",
            })
            cek("dosen dibuat", res.status_code == 200, res.text[:120])
            c.get("/login")
            csrf = c.cookies.get("absensi_v2_csrf") or ""
            c.post("/login", data={"username": "dosen", "password": "DosenSmokeTest123",
                                   "auth_csrf": csrf})
            me = c.get("/api/auth/me").json()
            cek("dosen login", me.get("role") == "dosen")
            res = c.get("/api/admin/mahasiswa")
            cek("dosen ditolak di API admin", res.status_code == 403,
                f"status={res.status_code}")
            res = c.get("/api/dosen/ringkasan")
            cek("ringkasan dosen terbaca", res.status_code == 200)
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print(f"\n=== Ringkasan: {langkah - len(gagal)}/{langkah} pemeriksaan berhasil ===")
    if gagal:
        print("Gagal:")
        for g in gagal:
            print(f"  - {g}")
        return 1
    print("Semua pemeriksaan lulus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
