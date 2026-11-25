# Sistem Absensi Mahasiswa Multi-Face Berbasis Face Recognition (V2)

Aplikasi web absensi perkuliahan berbasis pengenalan wajah (**MTCNN + InceptionResnetV1 / VGGFace2**) yang dirancang untuk skenario **multi-wajah dalam satu frame** tanpa login mahasiswa.

> **Catatan Privasi:** Repositori ini **tidak menyertakan** foto wajah, vektor embedding biometrik (`gallery.npz`), daftar identitas mahasiswa (`gallery_meta.json`), maupun database lokal. Anda dapat langsung mencoba seluruh fitur menggunakan **mode demo** dan mendaftarkan wajah uji sendiri melalui panel Admin.

---

## Ringkasan Proyek & Arsitektur

Aplikasi ini adalah iterasi produksi (**V2**) dari riset pipeline pengenalan wajah (lihat folder [`notebooks/`](notebooks/) dan [`docs/AUDIT_V1.md`](docs/AUDIT_V1.md)):

1. **Riset & Kalibrasi (`notebooks/01`–`06`):**
   - Alignment kanonik berbasis bounding box detektor **MTCNN** (`160×160`, `crop_factor=1.15`, koreksi kemiringan/roll > `0.5°`) + fallback **CLAHE** dan **Gamma**.
   - Augmentasi data latih, pembuatan centroid embedding 512-dimensi (**InceptionResnetV1** pretrained **VGGFace2**, L2-normalized), dan kalibrasi ambang *cosine similarity*.
   - Hasil evaluasi riset (tercatat di [`artifacts/threshold.txt`](artifacts/threshold.txt)): ambang `0.695` (`TAR = 75,91%`, `FAR_rata = 0,94%`, latensi p95 `287 ms`).
2. **Aplikasi Web V2 (`app/`):**
   - Mahasiswa membuka `/absen` (tanpa login), memilih sesi terbuka, lalu kamera mengirim frame ke server secara berkala.
   - Server mendeteksi **semua wajah** dalam frame (`keep_all=True`), memproses embedding dalam satu *batch*, mencocokkan ke galeri aktif + enrollment database, lalu mencatat kehadiran secara **idempoten** (`UNIQUE(session_id, mahasiswa_id)`).
   - **Enrollment mandiri:** Admin dapat menambah mahasiswa dan mengunggah foto wajah langsung lewat UI; embedding dihitung oleh server dan disimpan di SQLite/PostgreSQL tanpa menyentuh artefak riset.

```text
Browser (/absen, /admin, /dosen)
   │  HTTPS/HTTP + Session Cookie (HttpOnly) + X-CSRF-Token
   ▼
FastAPI (app/main.py)
   ├── api/public_attendance.py   validasi sesi + rate limit + masking identitas publik
   ├── api/admin.py & dosen.py    RBAC (admin/dosen), enrollment wajah, koreksi & ekspor
   ├── face/pipeline.py           MTCNN + CLAHE/gamma + align 160x160
   ├── face/engine.py             InceptionResnetV1 (VGGFace2) batch embedding + cosine match
   ├── face/gallery.py            pemuat galeri opsional + merge enrollment dari DB
   └── db/engine.py               SQLite (WAL) / PostgreSQL + audit_log
```

---

## Fitur Utama

- **Absensi Multi-Face Real-Time (`/absen`):** Mendeteksi & mencatat beberapa mahasiswa sekaligus dalam satu frame kamera tanpa akun login mahasiswa; nama & NIM pada layar publik disamarkan.
- **Pendaftaran Wajah dari Web (`/admin`):** Admin dapat mendaftarkan mahasiswa baru dan mengunggah foto wajah untuk langsung dikenali tanpa membangun ulang `gallery.npz`.
- **Manajemen Kelas, Sesi & Koreksi Bertaudit:** Buka/tutup sesi perkuliahan, keanggotaan kelas, koreksi status (`HADIR` / `TOLAK` / `MANUAL`) dengan alasan wajib, dan jejak `audit_log`.
- **RBAC Admin & Dosen:** Pembatasan akses kelas per dosen ditegakkan di backend pada level router & service.
- **Ekspor Laporan:** Unduh rekap absensi ke **CSV** dan **Excel (`.xlsx`)** dengan proteksi *formula injection*.
- **Keamanan:** Hash sandi **Argon2**, sesi opaque di database, proteksi **CSRF** (HMAC-SHA256 & *double-submit cookie*), rate limiter, dan security headers.

---

## Struktur Proyek

```text
Dev2/
├─ app/
│  ├─ main.py              # entry point FastAPI + middleware keamanan
│  ├─ config.py            # konfigurasi environment (.env)
│  ├─ api/                 # router: public_attendance, admin, dosen, auth, health
│  ├─ core/                # security (argon2, CSRF, rate limit) & deps (RBAC)
│  ├─ db/                  # skema DDL & abstraksi SQLite / PostgreSQL
│  ├─ face/                # pipeline MTCNN, engine InceptionResnetV1, gallery loader
│  ├─ services/            # logika bisnis (attendance, mahasiswa, dosen, kelas, laporan)
│  └─ web/ & static/       # template Jinja2 + CSS/JS vanilla (tanpa build step)
├─ notebooks/              # notebook riset 01-06 (tanpa output/data pribadi)
├─ docs/                   # ARSITEKTUR_V2.md, AUDIT_V1.md, OPERASIONAL.md, PANDUAN.md
├─ artifacts/
│  └─ threshold.txt        # ambang keputusan & metrik kalibrasi (tanpa data wajah)
├─ scripts/
│  ├─ seed_demo.py         # buat kelas & sesi demo fiktif untuk uji coba cepat
│  ├─ init_db.py           # inisialisasi skema database
│  ├─ seed_from_roster.py  # (opsional) impor dari gallery_meta.json lokal
│  ├─ backup_db.py         # backup & restore SQLite konsisten
│  └─ recover_admin.py     # reset sandi admin lokal
├─ tests/                  # suite unittest (unit, integrasi, keamanan, RBAC)
├─ Dockerfile              # container image siap jalankan
├─ requirements.txt
└─ .env.example
```

---

## Cara Menjalankan & Mencoba (Quick Start)

### Opsi A — Lokal (Python 3.10+ / teruji di 3.14)

> **Penting:** `facenet-pytorch==2.6.0` harus dipasang dengan `--no-deps` agar tidak bentrok dengan versi `numpy`/`torch`/`Pillow` terbaru di `requirements.txt`.

```powershell
# 1. Buat virtual environment & pasang dependensi
python -m venv .venv
.venv\Scripts\activate              # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
pip install --no-deps facenet-pytorch==2.6.0

# 2. Siapkan konfigurasi & data demo fiktif
Copy-Item .env.example .env         # Linux/macOS: cp .env.example .env
python -m scripts.seed_demo

# 3. Jalankan server
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

> Saat pertama kali dijalankan tanpa `model/inception_resnet_v1_vggface2.pt` lokal, engine otomatis mengunduh bobot pretrained **VGGFace2** melalui cache PyTorch/facenet-pytorch.

### Opsi B — Docker

```bash
docker build -t absensi-face-v2 .
docker run -p 8010:8010 -e SECRET_KEY="ganti-dengan-string-acak-panjang" absensi-face-v2
```

Untuk mengisi kelas & sesi demo di dalam container:
```bash
docker exec -it <container_id> python -m scripts.seed_demo
```

---

## Langkah Uji Coba di Browser

1. Buka `http://127.0.0.1:8010` → Anda akan diarahkan ke `/setup` untuk membuat akun **Admin** pertama.
2. Masuk ke `/admin` → buka tab **Mahasiswa** → pilih **Pengguna Demo 1** (atau tambah mahasiswa baru) → klik **Daftarkan wajah** dan unggah foto wajah Anda sendiri.
3. Buka `/absen` → pilih sesi **`DEMO-A`** yang sudah terbuka → izinkan kamera → wajah Anda akan terdeteksi dan tercatat hadir.

*(Catatan: akses kamera browser di luar `localhost` mewajibkan **HTTPS**.)*

---

## Pengujian

```powershell
python -m unittest discover -s tests -t .
```

Tes integrasi multi-wajah yang memakai dataset riset lokal (`tests/test_multi_face.py`) otomatis dilewati (`@unittest.skipUnless`) bila folder dataset tidak tersedia, sehingga seluruh suite tes tetap aman dijalankan di lingkungan bersih.

---

## Dokumentasi Lengkap

- [docs/ARSITEKTUR_V2.md](docs/ARSITEKTUR_V2.md) — arsitektur sistem, skema database, daftar endpoint, dan model keamanan.
- [docs/PANDUAN.md](docs/PANDUAN.md) — panduan penggunaan untuk Mahasiswa, Admin, dan Dosen.
- [docs/OPERASIONAL.md](docs/OPERASIONAL.md) — operasional, backup, performa, serta catatan keterbatasan (termasuk ketiadaan *liveness / anti-spoofing*).
- [docs/AUDIT_V1.md](docs/AUDIT_V1.md) — catatan audit teknis dari V1 ke V2.
