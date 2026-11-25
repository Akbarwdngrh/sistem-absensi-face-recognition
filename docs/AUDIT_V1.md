# Audit Read-Only — Proyek V1 & Artefak Model

Status: **Fase 1 selesai** · Tanggal audit: 2026-10-20 · Auditor: Senior Engineer (AI/ML + Fullstack)

> Semua pemeriksaan dilakukan **read-only**. Tidak ada file di
> `C:\Users\Acer\Documents\kapitaselekta\Dev` (V1),
> `C:\Users\Acer\Documents\kapitaselekta\model` (model), maupun
> `C:\Users\Acer\Documents\Kapita` (artefak riset) yang diubah.
> Basis data V1 hanya dibuka dengan URI `mode=ro`.

---

## 1. Struktur proyek V1 & entry point

```
C:\Users\Acer\Documents\kapitaselekta\Dev\
├─ app.py              # 1563 baris — aplikasi FastAPI: seluruh rute, otorisasi, CSRF
├─ config.py           # konfigurasi via .env / environment variable
├─ database.py         # skema SQLite + helper akses data (SQL mentah, parameterized)
├─ inference.py        # adapter pipeline wajah (FROZEN, read-only artefak)
├─ roster.py           # roster identitas dari gallery_meta.json (read-only)
├─ security.py         # argon2 hashing, token sesi, kebijakan kata sandi
├─ utils.py            # parser identitas + ekspor CSV/XLSX
├─ email_service.py    # rekap SMTP idempoten
├─ seed.py             # seeding kelas/keanggotaan dari gallery_meta.json
├─ merge_classes.py    # utilitas penggabungan label kelas
├─ recover_admin.py    # pemulihan kata sandi admin (interaktif, lokal)
├─ requirements.txt    # 15 paket + catatan facenet-pytorch --no-deps
├─ .env / .env.example # konfigurasi (secret ada di .env V1 — TIDAK disalin ke V2)
├─ templates/          # 13 template Jinja2 (base, login, setup, dashboard, attendance, …)
├─ static/css/app.css  # design system (19 KB)
├─ static/js/          # app.js (shell/rail), camera.js (alur kamera V1)
├─ tests/              # 73 tes unittest
├─ data/absensi.db     # SQLite V1 (151 KB) + 1 backup
├─ exports/            # hasil ekspor CSV/XLSX
└─ docs/ARSITEKTUR.md  # dokumen arsitektur V1
```

**Entry point:** `app:app` (objek `FastAPI` di `app.py:45`) → `uvicorn app:app --port 8000`.
Dokumentasi OpenAPI dimatikan (`docs_url=None, redoc_url=None`).

---

## 2. Teknologi yang benar-benar digunakan

| Lapisan | Teknologi | Bukti |
|---|---|---|
| Backend | **FastAPI 0.143.3** + Uvicorn 0.54.0 | `app.py`, `requirements.txt` |
| Template | **Jinja2 3.1.6** (server-side) | `templates/` |
| Frontend | Vanilla JS + CSS kustom (Tanpa SPA/framework, tanpa npm) | `static/js/app.js`, `camera.js` |
| Database | **SQLite 3** (`sqlite3` stdlib), WAL, `PRAGMA foreign_keys=ON` | `database.py` |
| Auth | Cookie sesi opaque + tabel `auth_sessions`, **argon2-cffi** | `security.py`, `app.py:104-116` |
| CSRF | HMAC-SHA256(SECRET_KEY, token) + double-submit cookie untuk halaman publik | `app.py:81-82, 169-187` |
| ML | **PyTorch 2.14.1+cu126**, **facenet-pytorch 2.6.0**, InceptionResnetV1 | `inference.py:231-283` |
| CV | **OpenCV 4.14.0.94**, Pillow 12.3.0, pillow-heif 1.8.0 | `inference.py` |
| Ekspor | pandas 3.0.6 + openpyxl 3.1.5 | `utils.py:144-158` |
| Email | SMTP via `smtplib` stdlib | `email_service.py` |

Tidak ada: Docker, Celery, Redis, Alembic, ORM/SQLAlchemy, JS build step, Node.js.
Manajemen skema = SQL idempotent (`CREATE TABLE IF NOT EXISTS`) + `init_db()` saat startup.

---

## 3. Model Machine Learning & pipeline pengenalan wajah

### 3.1 Artefak yang dipakai aplikasi V1 (READ-ONLY)

| Artefak | Lokasi | Isi | Peran |
|---|---|---|---|
| `inception_resnet_v1_vggface2.pt` | `kapitaselekta\model\` (112 MB) | `state_dict` 716 key, `last_linear (512,1792)`, `logits (8631,512)` | Bobot embedding wajah |
| `gallery.npz` | `Kapita\` | `embeddings (69,512) float32`, `person (69,)`, `aktif (69,) bool`, `model='vggface2'`, `img_size=160` | Galeri centroid + label |
| `gallery_meta.json` | `Kapita\` | 69 entri `{person, nama, nim, kelas, n_file_latih, n_grup_latih, aktif}` | Identitas biometrik |
| `threshold.txt` | `Kapita\` | baris 1 = `0.695000`; metadata: TAR 0.7591, FRR 0.2409, FAR_rata 0.00941, FAR_maks 0.3011, EER@0.705 | Ambang cosine |

Verifikasi independen (dilakukan pada audit, read-only):
- `InceptionResnetV1(pretrained=None, classify=True, num_classes=8631)` +
  `load_state_dict(..., strict=True)` → **berhasil**.
- `FaceEngine.ensure_loaded()` → `loaded=True`, `device=cuda`, `n_persons=69`, `threshold=0.695`.
- Beban kerja: 13 entri metadata tanpa NIM; sebaran kelas SKU7A 33, SK7A 23, SKPROF 7, SKALIHPROGRAM 1, P1 1, "-" 4.

### 3.2 Pipeline kanonik (harus dipertahankan di V2)

Disalin persis dari `01_preprocessing_wajah.ipynb` dan `Kapita\live_absensi_v2.py`:

1. `downscale_max_side(img, 1600)` (INTER_AREA).
2. **MTCNN** `keep_all=True, select_largest=True, min_face_size=20` (+ `landmarks=True`).
   Fallback: CLAHE (`clipLimit=2.0, tile=(8,8)`) → MTCNN; lalu gamma `g=0.5` → MTCNN longgar
   (`min_face_size=10, thresholds=[0.5,0.6,0.5]`).
3. `align_face`: bbox **detektor** (bukan MTCNN landmark box), `crop_factor=1.15`, kanvas **160×160**,
   koreksi roll bila > 0.5° (landmark[0]→landmark[1]).
4. Normalisasi `(pixel − 127.5) / 128`, RGB.
5. **InceptionResnetV1 (VGGFace2)** → 512-d → **L2-normalized**.
6. **Cosine similarity** `centroid · emb` (centroid galeri sudah ternormalisasi) → `argmax`.
7. Kecocokan bila `similarity ≥ 0.695`. Skor adalah **similarity, bukan probabilitas**.

Kunci kompatibilitas: urutan BGR→RGB, `crop_factor=1.15` (bukan margin 0.4 dari `face_utils.py`,
yang dipakai eksperimen **v2 terpisah** dengan model ResNet berbeda — bukan yang dipakai V1).

### 3.3 Artefak yang TIDAK dipakai V1 (jangan dipakai V2 tanpa alasan)

| Artefak | Kenapa tidak dipakai |
|---|---|
| `kapitaselekta\model\face_recognition_artifact.pkl` | Artefak riset: 647 embedding, **SVC linear** + `LabelEncoder`, `threshold_cosine=0.6`. Berbeda dari pipeline V1 (centroid cosine, 0.695). Memakainya = menggeser ambang & representasi. |
| `Kapita\model\model_absensi.pth` (9.5 MB) | Model klasifikasi v2 (ResNet + `model_absensi_v2.pth`), `face_utils.py` (Haar cascade, IMG_SIZE 224, mean/std ImageNet). Compatibilitas nol dengan embedding V1. |
| `Kapita\model\label_map*.json` | Metadata label eksperimen v2. |

---

## 4. Struktur database V1 & mekanisme pencatatan absensi

Tabel V1 (SQLite, `database.py:17-121`):

```
users(id, username⚿, password_hash, role∈{mahasiswa,dosen,admin}, nama, email,
      nim, person_label, aktif, created_at, updated_at)
      + UNIQUE lower(username), UNIQUE partial lower(nim) bila terisi
classes(id, kode_mk, nama_mk, label, dosen_id→users, dosen_nama, dosen_email,
        status∈{aktif,nonaktif}, …, UNIQUE(kode_mk,label))
class_members(id, class_id→classes, mahasiswa_id→users, created_at,
              UNIQUE(class_id, mahasiswa_id))
attendance_sessions(id, class_id→classes, tanggal, mulai_at, selesai_at,
                    status∈{buka,tutup}, keterangan, created_by→users, …,
                    UNIQUE(class_id,tanggal,mulai_at))
attendance_records(id, session_id→sessions, class_id→classes, mahasiswa_id→users,
                   status∈{HADIR,TOLAK,MANUAL}, similarity REAL,
                   metode∈{face,manual}, catatan, recorded_at, created_by,
                   UNIQUE(session_id, mahasiswa_id))   ← anti-duplikasi
email_logs(...), audit_logs(id, user_id, aksi, entitas, entitas_id, detail, created_at),
auth_sessions(token⚿, user_id→users, created_at, expires_at, user_agent)
```

Isi DB V1 saat audit (dibaca `mode=ro`): 1 admin, 1 dosen, 69 mahasiswa, 2 kelas,
65 anggota kelas, 2 sesi (1 buka / 1 tutup), 5 catatan absensi (semua `HADIR`/`face`).

**Mekanisme pencatatan V1 (`app.py:958-1067`):**
`POST /api/attendance/checkin` → wajib **login mahasiswa** → validasi sesi `buka` → validasi
anggota kelas → decode gambar → `inference.engine.recognize()` → **binding identitas wajib cocok
dengan akun yang login** (`person_label` atau `nim`) → cek record existing → `INSERT` dengan
`UNIQUE(session_id, mahasiswa_id)` → audit. Wajah tak dikenal/mismatch tidak menulis apa pun.

**Keterbatasan V1 yang menjadi target V2:** mahasiswa harus login (bertentangan dengan V2),
`pick_largest_face` memproses **hanya 1 wajah per frame** (`inference.py:159-164`), tidak ada
tracking/debounce multi-orang, dan tidak ada multi-face attendance.

---

## 5. Komponen yang dapat dipakai ulang oleh V2

| Komponen | Cara reuse |
|---|---|
| `inference.py` (konstanta & fungsi) | **Copy logika** ke `v2/face/` V2 (diboleh: writes hanya di Dev2), dengan penyesuaian: `pick_largest_face` → iterasi **semua** box. Bobot/artefak tetap dibaca dari lokasi aslin. |
| Model & galeri | Dibaca **langsung** dari `kapitaselekta\model` + `Kapita\` (read-only). Tidak disalin, tidak ditulis. |
| `security.py` | Copy verbatim (argon2 + token sesi + validasi kata sandi). Standar library, tanpa dependensi baru. |
| `roster.py` | Copy + adaptasi (dipakai V2 untuk impor roster & validasi NIM opsional). |
| `utils.py` (ekspor + sanitasi formula injection) | Copy, tambah kolom V2 (kelas, sesi, sumber). |
| `templates/*.html` + `static/css/app.css` | **Gunakan ulang sebagai basis** disalin ke Dev2 (desain sudah mobile-first & aksesibel), lalu perluas untuk alur publik + multi-face. |
| `seed.py` | Adaptasi: seed roster → mahasiswa/kelas/keanggotaan V2 (opsional, eksplisit). |
| `database.py` | Pola `get_conn`/`immediate()`/`audit()` di-*rebuild* dengan skema V2 (entitas terpisah `mahasiswa`, `dosen`, `users`). |
| `docs/ARSITEKTUR.md` | Pola penulisan dokumen V2. |

---

## 6. Risiko teknis khusus multi-face recognition

1. **Detektor multi-wajah — RISIKO RENDAH, SUDAH DIBUKTI.**
   `facenet-pytorch` 2.6.0 `MTCNN.detect()` dengan `keep_all=True, select_largest=True`
   (`models/mtcnn.py:328-334`) **mengembalikan SEMUA box**, diurutkan dari luas terbesar ke
   terkecil — `select_largest` hanya mengurutkan, bukan memotong, selama `keep_all=True`.
   Bukti eksperimen audit: komposit 2 wajah → `n boxes = 2`, keduanya diproses independen
   (wajah Audrey recognised similarity **0.785**; wajah kedua 0.338 → `unknown`, tidak salah
   identitas). **Kesimpulan: multi-face detection dapat dibangun di atas MTCNN yang sama
   tanpa mengganti model.**
2. **Multi-face recognition — RISIKO RENDAH.** Embedding = batch forward InceptionResnetV1
   pada N wajah ter-align (identik secara matematis dengan embedding per-wajah, `F.normalize`
   per baris). Skor cosine per wajah terhadap centroid galeri **sama persis** dengan pipeline V1
   (normalisasi L2 ⇒ cosine = dot product).
3. **Multi-face attendance — RISIKO SEDANG.** Butuh: (a) prevents duplikasi lintas-frame,
   (b) memetakan banyak wajah ke banyak mahasiswa berbeda dalam satu frame,
   (c) identifier_face→mahasiswa mapping. Butuh `UNIQUE(session_id, mahasiswa_id)` +
   transaksi `BEGIN IMMEDIATE` + cooldown per identitas.
4. **Deteksi wajah dekat/terhalang** — MTCNN bisa gagal bila wajah kecil (< 20 px) atau
   tertutup. Mitigasi: `min_face_size` tetap 20 (konsisten) + fallback CLAHE/gamma sudah ada.
5. **Performa** — MX150 hanya **2.15 GB VRAM**; p95 latensi 1 wajah ≈ **287 ms** (evaluasi riset).
   Batched N wajah ≈ 1× forward; deteksi MTCNN dominan. Strategi: throttle inferensi
   (1 proses/in-flight), kotak RETANGGAL (bbox) untuk menyalakan kamera, bukan area sempit.
6. **Ambang** — `0.695` menghasilkan FAR_rata 0.94% tapi **FAR_maks 30%** pada sebagian pasangan.
   V2 **tidak boleh** menurunkan ambang; cukup prepend "unknown" + minta verifikasi admin.
7. **Anti-spoofing** — tidak ada liveness di V1 maupun artefak yang tersedia (MTCNN/ArcFace
   murni 2D). Dokumentasikan sebagai **keterbatasan**, jangan klaim kebal spoofing.
8. **Gallery face ↔ DB** — V1 mengikat `users.person_label` → `gallery_meta.json`.
   V2 perlu tabel `face_enrollments` (mahasiswa_id, person_label, sumber, aktif).

---

## 7. Rancangan arsitektur V2 (ringkas, detail di `docs/ARSITEKTUR_V2.md`)

```
Dev2/
├─ app/                     # paket aplikasi (bukan app.py monolit)
│  ├─ main.py               # FastAPI app + startup/lifespan
│  ├─ config.py             # env/.env khusus V2 (tidak mewarisi V1)
│  ├─ db/                   # schema.sql, engine.py (SQLite→PostgreSQL friendly), migrations/
│  ├─ core/                 # security.py (argon2), sessions.py, csrf.py, audit.py, ratelimit.py
│  ├─ models/               # dataclass/pydantic domain
│  ├─ schemas/              # pydantic request/response
│  ├─ services/             # students, lecturers, classes, sessions, attendance, reports
│  ├─ face/                 # pipeline.py (multi-face), engine.py, gallery.py, thresholds.py
│  ├─ api/                  # routers: auth, public_attendance, admin, lecturer, reports, health
│  └─ web/                  # pages (Jinja2) + templates/
├─ data/                    # absensi_v2.db (SQLite V2)
├─ exports/                 # hasil ekspor V2
├─ tests/                   # unittest (unit + integrasi + multi-face)
├─ scripts/                 # init_db.py, seed_from_roster.py, recover_admin.py, backup_db.py
├─ docs/                    # ARSITEKTUR_V2.md, AUDIT_V1.md, PANDUAN.md, OPERASIONAL.md
├─ .env.example             # tanpa secret
├─ requirements.txt
└─ README.md
```

Prinsip yang dipertahankan dari V1: **backend satu-satunya pengambil keputusan**;
frontend hanya mengirim frame; identitas/skor/status ditentukan server; artefak model
dibaca read-only; tidak menyimpan frame wajah.

---

## 8. Daftar pekerjaan V2

**Perkakas & konfigurasi** — venv Dev2 (tidak mengubah global), `config.py` V2, `.env.example`,
`requirements.txt` V2, health check.

**Database V2** — skema baru terpisah (`mahasiswa`, `dosen`, `users`, `kelas`, `kelas_mahasiswa`,
`kelas_dosen`, `face_enrollments`, `sesi_absensi`, `kehadiran`, `audit_log`, `auth_sessions`),
`UNIQUE(sesi_id, mahasiswa_id)`, FK, indeks, transaksi.

**Autentikasi & RBAC** — login/logout admin & dosen, sesi opaque + cookie HttpOnly,
CSRF double-submit, argon2, `require_roles` di backend, rate limit login.

**Halaman publik mahasiswa** — daftar sesi aktif (tanpa PII), pilih sesi, preview kamera,
indikator jumlah wajah, status per wajah, tanpa login.

**Mesin wajah multi-face** — `face/pipeline.py`: deteksi semua wajah → align (kanonik V1) →
batch embed → cosine vs galeri → per-face result (identity / unknown / error).

**Pencatatan otomatis** — service attendance: validasi sesi buka + anggota kelas + duplikasi
(transaksi `BEGIN IMMEDIATE`), catat metadata (similarity, det_status, jumlah wajah, sumber).

**Dashboard admin** — statistik, CRUD mahasiswa/dosen/kelas/anggota, sesi buka/tutup,
tabel absensi (filter + paginasi), koreksi absensi **wajib audit trail**, ekspor CSV/XLSX,
log audit, pengaturan.

**Dashboard dosen** — ringkasan kelas yang diampu, buka/tutup sesi (jika diberi kewenangan),
rekap & ekspor kelasnya saja, daftar mahasiswa & status.

**Keamanan** — validasi input, rate limit, proteksi path, tidak kirim embedding ke browser,
tanpa penyimpanan frame, HTTPS note, backup/restore.

**Pengujian** — unit (skema, RBAC, attendance), integrasi (alur sesi/pencatatan/duplikasi),
multi-face (deteksi >1 wajah, proses independen, tidak duplikat), non-regresi (V1 utuh).

---

## 9. Kesimpulan & keputusan yang perlu persetujuan

**Aman & terbukti (tanpa persetujuan tambahan):**
- Multi-face detection memakai MTCNN yang sama (`keep_all=True`) — terbukti 2 wajah terdeteksi.
- Threshold `0.695` dipertahankan; preprocessing kanonik V1 disalin apa adanya.
- Model dibaca dari lokasi aslin; tidak ada penulisan ke `kapitaselekta\model` maupun `Kapita`.

**Perlu keputusan pemilik (diajukan sebelum menulis kode):**
1. **Sumber data awal DB V2** — kosong + impor roster opsional, atau seed langsung dari roster.
2. **Isolasi dependensi** — venv dengan `--system-site-packages` (reuse torch global, cepat)
   vs venv benar-benar terisolasi (unduh torch ~2,5 GB, risiko mismatch).
3. **Pengelolaan wajah** — V2 hanya mereferensikan galeri 69 orang yang ada, atau
   admin boleh enrolling wajah baru (embedding dihitung V2, disimpan di DB V2, tidak
   menyentuh artefak model).
4. **Nomor port V2** — 8010 (default) agar tidak bentrok dengan V1 (8000).
