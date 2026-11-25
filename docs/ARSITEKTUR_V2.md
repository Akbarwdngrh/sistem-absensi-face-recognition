# Arsitektur V2

Dokumen ini merangkum struktur, aliran data, skema basis data, daftar endpoint,
dan batas kepercayaan aplikasi V2.

---

## 1. Komponen

```
Browser (HTML/CSS/JS vanilla)
   │  HTTP + cookie sesi (HttpOnly) + header X-CSRF-Token
   ▼
FastAPI (app/main.py)
   ├── web/pages.py        halaman Jinja2: login, setup, /absen, /admin, /dosen
   ├── api/auth.py         status sesi, logout, status engine
   ├── api/public_attendance.py   sesi terbuka + proses frame multi-wajah (PUBLIK)
   ├── api/admin.py        CRUD & laporan (role admin)
   ├── api/dosen.py        rekap kelas sendiri (role dosen)
   ├── api/health.py       /health
   ├── core/security.py    argon2, token sesi, CSRF, rate limit, masking
   ├── core/deps.py        sesi, RBAC, helper CSRF
   ├── services/           absensi, mahasiswa, dosen, kelas, laporan
   ├── face/pipeline.py    preprocessing kanonik (align, CLAHE, gamma, normalisasi)
   ├── face/engine.py      MTCNN multi-wajah + embedding batch + pencocokan
   ├── face/gallery.py     galeri READ-ONLY + ambang
   └── db/engine.py        SQLite/PostgreSQL + skema + audit
        │  read-only
        ▼
   Artefak riset (di luar Dev2):
   gallery.npz · gallery_meta.json · threshold.txt · inception_resnet_v1_vggface2.pt
```

Prinsip yang dipertahankan dari V1: **backend adalah satu-satunya pengambil
keputusan**. Browser hanya mengirim frame dan menampilkan hasil.

---

## 2. Batas kepercayaan

| Lapisan | Dipercaya? | Catatan |
|---|---|---|
| Cookie `absensi_v2_session` | Ya (server yang menerbitkan) | HttpOnly, SameSite=Lax, opsional Secure |
| Header `X-CSRF-Token` | Ya (terikat token sesi + SECRET_KEY) | Wajib untuk semua mutasi terautentikasi |
| Body request (identitas, status, skor) | **Tidak** | Server mengabaikan nilai identitas dari klien |
| Frame gambar | Tidak, kecuali lolos verifikasi | Batas ukuran, decode, dan deteksi |
| Hasil `engine.recognize_all` | Ya | Identitas tetap diverifikasi ke DB (keanggotaan kelas) |
| Halaman publik | Publik | Identitas hanya ditampilkan dalam bentuk tersamar |

---

## 3. Skema basis data V2

```
users(id, username⚿lower, password_hash, role∈{admin,dosen}, nama, email, nim,
      aktif, created_at, updated_at)

auth_sessions(token⚿, user_id→users, created_at, expires_at, user_agent, ip)

mahasiswa(id, nim⚿lower, nama, email, no_hp, aktif, dibuat_oleh→users,
          created_at, updated_at)

dosen(id, user_id→users, nid, nama, email, no_hp, aktif, created_at, updated_at)

kelas(id, kode_mk, nama_mk, label, dosen_id→dosen, tahun_akademik, semester,
      status∈{aktif,nonaktif}, created_at, updated_at, UNIQUE(kode_mk,label))

kelas_mahasiswa(id, class_id→kelas, mahasiswa_id→mahasiswa, created_at,
                UNIQUE(class_id, mahasiswa_id))

kelas_dosen(id, class_id→kelas, dosen_id→dosen, created_at,
            UNIQUE(class_id, dosen_id))

face_enrollments(id, mahasiswa_id→mahasiswa, person_label, sumber∈{gallery,upload,manual},
                 embedding BLOB, dimensi, aktif, catatan, created_at, updated_at,
                 UNIQUE(mahasiswa_id, person_label))

sesi_absensi(id, class_id→kelas, tanggal, mulai_at, selesai_at, status∈{buka,tutup},
             keterangan, created_by→users, created_at, updated_at,
             UNIQUE(class_id, tanggal, mulai_at))

kehadiran(id, session_id→sesi_absensi, class_id→kelas, mahasiswa_id→mahasiswa,
          status∈{HADIR,TOLAK,MANUAL}, similarity REAL, metode∈{face,manual},
          sumber∈{kamera,manual,impor}, det_status, n_wajah, bbox, catatan,
          recorded_at, created_by→users, updated_at,
          UNIQUE(session_id, mahasiswa_id))      ← anti-duplikasi

audit_log(id, user_id→users, aksi, entitas, entitas_id, detail, ip, created_at)

pengaturan(key⚿, value, updated_at) · schema_version(version, applied_at)
```

Perbedaan penting dari V1: **mahasiswa tidak lagi menjadi baris `users`**.
Data mahasiswa dipisah ke tabel `mahasiswa`, sehingga tidak ada jalur login
mahasiswa sama sekali.

### Portabilitas ke PostgreSQL

`app/db/engine.py` mengabstraksi perbedaan:

* placeholder `?` → `%s` (psycopg);
* `INTEGER PRIMARY KEY` → `BIGSERIAL PRIMARY KEY`;
* `lastrowid` → `RETURNING id`;
* `BEGIN IMMEDIATE` → `SERIALIZABLE`;
* `ON CONFLICT ... DO NOTHING` dipakai untuk idempotensi (didukung kedua mesin).

Aktifkan dengan `DATABASE_URL=postgresql://...` + `pip install "psycopg[binary]"`
di venv V2. Skema tidak perlu diubah.

---

## 4. Daftar endpoint

### Publik
| Method | Path | Fungsi |
|---|---|---|
| GET | `/health`, `/api/health` | Status aplikasi (tanpa detail internal) |
| GET | `/absen` | Halaman absensi mahasiswa |
| GET | `/api/public/sessions` | Sesi yang sedang dibuka (tanpa PII) |
| GET | `/api/public/sessions/{id}` | Detail sesi + validasi penerimaan absensi |
| POST | `/api/public/attendance/frame` | Proses frame multi-wajah → pencatatan |

### Autentikasi
| Method | Path | Fungsi |
|---|---|---|
| GET/POST | `/login` | Login admin/dosen (form + CSRF double-submit) |
| GET | `/logout` | Akhiri sesi |
| GET/POST | `/setup` | Pembuatan admin pertama (hanya bila belum ada admin) |
| GET | `/api/auth/me` | Profil sesi berjalan |
| POST | `/api/auth/logout` | Logout via JSON |
| GET | `/api/auth/engine` | Status model (device, jumlah identitas, ambang) |

### Admin (`role=admin`)
| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/admin/stats` | Ringkasan dashboard |
| GET/POST | `/api/admin/mahasiswa` | Daftar & tambah mahasiswa |
| GET/PUT/DELETE | `/api/admin/mahasiswa/{id}` | Detail, ubah, hapus |
| POST | `/api/admin/mahasiswa/{id}/kelas` | Tambah/lepas anggota kelas |
| GET/POST | `/api/admin/mahasiswa/{id}/wajah` | Enrollment wajah baru (embeddings di DB V2) |
| DELETE | `/api/admin/wajah/{id}` | Hapus enrollment |
| GET/POST | `/api/admin/dosen` | Daftar & tambah dosen (+ akun login) |
| PUT/DELETE | `/api/admin/dosen/{id}` | Ubah/hapus dosen |
| POST | `/api/admin/dosen/{id}/password` | Reset kata sandi (sesi dihentikan) |
| POST | `/api/admin/dosen/{id}/kelas` | Penetapan dosen ke kelas |
| GET/POST | `/api/admin/kelas` | Daftar & tambah kelas |
| GET/PUT/DELETE | `/api/admin/kelas/{id}` | Detail, ubah, hapus |
| GET/POST | `/api/admin/sesi` | Daftar & buka sesi absensi |
| POST | `/api/admin/sesi/{id}/tutup` · `/buka` | Tutup/buka kembali sesi |
| GET | `/api/admin/kehadiran` | Rekap (filter + paginasi) |
| PUT | `/api/admin/kehadiran/{id}` | Koreksi (wajib audit) |
| POST | `/api/admin/kehadiran/manual` | Catat manual |
| DELETE | `/api/admin/kehadiran/{id}` | Hapus catatan (wajib alasan) |
| GET | `/api/admin/audit` | Log aktivitas |
| GET | `/api/admin/export` | Ekspor CSV/XLSX |

### Dosen (`role=dosen`, dibatasi kelas yang diampu)
| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/dosen/ringkasan` | Ringkasan kelas & sesi |
| GET | `/api/dosen/kelas/{id}/kehadiran` | Rekap satu kelas |
| GET | `/api/dosen/kelas/{id}/daftar` | Daftar mahasiswa + status |
| GET | `/api/dosen/kehadiran` | Rekap yang difilter |
| PUT | `/api/dosen/kehadiran/{id}` | Koreksi (wajib audit) |
| POST | `/api/dosen/sesi` · `/sesi/{id}/tutup` | Buka/tutup sesi kelas sendiri |
| GET | `/api/dosen/export` | Ekspor kelas sendiri |

---

## 5. Alur pengenalan wajah

```
frame JPEG ──► downscale (maks 1600 px)
          ──► MTCNN(keep_all=True, select_largest=True, min_face_size=20) + landmarks
                fallback: CLAHE → MTCNN, lalu gamma(0.5) → MTCNN longgar
          ──► untuk tiap box (urut luas terbesar, maks 12):
                align_face(crop_factor=1.15, roll>0.5°, kanvas 160×160)
                batch embed: InceptionResnetV1(VGGFace2) → L2-normalize
                cosine similarity ke centroid galeri → argmax → skor ≥ 0.695 ?
```

Konstanta kanonik (identik V1): `IMG_SIZE=160`, `CROP_FACTOR=1.15`,
normalisasi `(px−127.5)/128`, ambang `0.695`. **Diuji:** untuk wajah yang sama,
V2 menghasilkan `person` dan `similarity` yang identik dengan V1
(`tests/test_multi_face.py::test_pipeline_v2_kompatibel_dengan_v1`).

Batas `MAX_FACES_PER_FRAME` (default 12) menjaga beban CPU/GPU; wajah di luar
batas diabaikan, bukan diperbaiki dengan mengubah ambang.

---

## 6. Alur pencatatan (anti-duplikasi berlapis)

1. Rate limit per IP.
2. CSRF double-submit.
3. Sesi harus `buka` + kelas `aktif` → selain itu `409`.
4. Ukuran & format frame divalidasi.
5. Deteksi + pengenalan multi-wajah.
6. Wajah tak dikenal → tidak ada catatan.
7. Wajah dikenal tetapi bukan anggota kelas → tidak ada catatan (`tidak_anggota`).
8. Cek cepat `kehadiran` yang sudah ada → `sudah_tercatat`.
9. Transaksi eksklusif (`BEGIN IMMEDIATE`) + `ON CONFLICT (session_id,
   mahasiswa_id) DO NOTHING` → jaring pengaman terakhir bila ada balapan.
10. Cooldown per identitas (default 3 detik) mengurangi pemrosesan ulang.
11. Audit ditulis untuk kejadian penting.

---

## 7. Keamanan

- **Autentikasi**: argon2-cffi; token sesi acak 32 byte di tabel `auth_sessions`;
  cookie HttpOnly/SameSite=Lax; `Secure`_opsional; rehash otomatis bila parameter
  argon2 berubah.
- **Otorisasi**: `deps.require_roles` + `ensure_class_access` (dosen). Router admin
  dan dosen memakai *router-level dependency* sehingga penolakan terjadi sebelum
  validasi body.
- **CSRF**: HMAC-SHA256(`SECRET_KEY`, token_sesi) via header `X-CSRF-Token`;
  double-submit cookie untuk halaman publik.
- **Rate limit**: sliding window in-memory (login, absensi publik, API umum).
- **Validasi input**: pydantic (batas panjang, format NIM/email/tanggal/jam).
- **SQL injection**: seluruh kueri memakai parameter binding; nama kolom
 _whitelist_ pada pembaruan dinamis.
- **XSS**: template Jinja2 auto-escape; `innerHTML` pada JS hanya untuk data
  yang sudah di-escape.
- **Formula injection**: sel ekspor diawali `= + - @` diberi prefix `'`.
- **Privasi biometrik**: embedding tidak pernah dikirim ke browser; frame tidak
  disimpan; identitas publik disamarkan.
- **Header**: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`,
  `Permissions-Policy: camera=(self)`.

---

## 8. Konkurensi & performa

- `FaceEngine` memakai `threading.Lock` untuk pemuatan dan `_infer_lock` untuk
  serialisasi inferensi → aman untuk beberapa request bersamaan dalam satu proses.
- `INFER_MIN_INTERVAL_MS` membatasi frekuensi inferensi; klien juga mengirim
  frame paling sering tiap ~1,5 detik dan hanya satu request in-flight.
- Embedding batch: N wajah diproses dalam **satu** forward pass.
- SQLite: WAL, `busy_timeout=30s`, transaksi eksklusif untuk operasi kritis.
- Diper parch pada MX150 (2 GB VRAM): muat model ~14 detik, p95 inferensi
  1 wajah ~0,3–0,6 detik (tergantung jumlah wajah & pencahayaan).

---

## 9. Titik ekstensi

- **Ambang**: `threshold.txt` (read-only) → `DEFAULT_THRESHOLD` sebagai cadangan.
  Perubahan ambang harus disertai uji akurasi/FAR.
- **Enrollment wajah baru**: admin mengunggah foto; V2 menghitung embedding dengan
  model yang sama dan menyimpannya di `face_enrollments` (DB V2), lalu mendaftarkannya
  ke galeri aktif di memori proses. Artefak riset tidak pernah ditulis.
- **Server database**: `DATABASE_URL` → PostgreSQL tanpa mengubah kueri.
- **Model**: mengganti InceptionResnetV1 berarti semua embedding galeri harus
  dihitung ulang — bukan perubahan kecil, perlu analisis terpisah.
