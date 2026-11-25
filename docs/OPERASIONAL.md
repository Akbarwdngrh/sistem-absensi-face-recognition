# Operasional, Keamanan, dan Keterbatasan V2

Dokumen ini menjawab: cara menjalankan & menghentikan V2 tanpa memengaruhi V1,
bagaimana mencadangkan data, apa yang sudah aman, dan **apa yang belum** aman.

---

## 1. Isolasi terhadap V1 — bukti

| Aspek | V1 (`...\kapitaselekta\Dev`) | V2 (`...\kapitaselekta\Dev2`) |
|---|---|---|
| Database | `Dev/data/absensi.db` | `Dev2/data/absensi_v2.db` (skema & entitas terpisah) |
| Konfigurasi | `Dev/.env` | `Dev2/.env` (tidak membaca konfigurasi V1) |
| Server | port 8000 | port 8010 (bawaan) |
| Venv | `Dev/.venv` | `Dev2/.venv` (terpisah; `--system-site-packages` agar model identik) |
| Log | (tidak ada) | `Dev2/logs/absensi_v2.log` |
| Ekspor | `Dev/exports` | `Dev2/exports` |
| Kode | `app.py` monolit | paket `app/` (core/db/face/services/api/web) |

Artefak model & riset (`kapitaselekta\model`, `Documents\Kapita`) **dibaca saja**
oleh V2: tidak ada satu pun operasi tulis ke sana. Setelah pengerjaan V2 selesai,
verifikasi bahwa `Dev/data/absensi.db` tidak berubah (ukuran & timestamp),
`model/*.pt`, `model/*.pkl`, `Kapita/gallery.npz`, `Kapita/gallery_meta.json`, dan
`Kapita/threshold.txt` tetap sama.

> Catatan: file `Dev/data/absensi.db-wal` / `-shm` adalah file samping WAL SQLite.
> File-file ini dapat tersentuh saat database dibuka (bahkan read-only), tetapi isi
> `absensi.db` tidak berubah dan jumlah baris tetap sama seperti saat audit.

### Hash artefak (tercatat 2026-10-20, setelah penyelesaian V2)

| File | SHA-256 |
|---|---|
| `kapitaselekta\model\inception_resnet_v1_vggface2.pt` | `1D165FD0F0FC10BC9CD36794338EB9B31F1CF23B1DDBB10D246B01DFF230475E` |
| `kapitaselekta\model\face_recognition_artifact.pkl` | `764491EEA23AECE35ED887721CF046FAB2BE765D3B9C24AF5F4ED3922B55EB4F` |
| `Kapita\gallery.npz` | `4EFBAD28F1FD3F85B9C0B8BFBAF56FD534F1B729E860773D5A24B53D4A3CB26C` |
| `Kapita\threshold.txt` | `FB3A290035E6B3B0AC91C2639ED9F9737F5A6A64ECFC7FD0CD3BF27812E5F668` |

Periksa ulang bila perlu membuktikan tidak ada perubahan:

```powershell
Get-FileHash "C:\Users\Acer\Documents\kapitaselekta\model\inception_resnet_v1_vggface2.pt" -Algorithm SHA256
```

---

## 2. Menjalankan & menghentikan

```powershell
cd C:\Users\Acer\Documents\kapitaselekta\Dev2
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Menghentikan: `Ctrl+C` pada terminal server (atau hentikan prosesnya).
Tidak ada service/jobs yang dibuat di luar folder V2, sehingga menghentikan V2
**tidak** menyentuh V1. Keduanya dapat berjalan bersamaan (port berbeda,
database berbeda, folder berbeda).

---

## 3. Cadangan & pemulihan

```powershell
# cadangan (VACUUM INTO -> salinan konsisten tanpa menghentikan aplikasi)
.\.venv\Scripts\python.exe -m scripts.backup_db
.\.venv\Scripts\python.exe -m scripts.backup_db --list

# pemulihan (menyimpan salinan database lama sebelum ditimpa)
.\.venv\Scripts\python.exe -m scripts.backup_db --restore data\backups\absensi_v2_20260101_080000.db
```

Cadangan disimpan di `Dev2/data/backups/`. Untuk PostgreSQL gunakan `pg_dump`
(`scripts/backup_db.py` menolak dengan pesan eksplisit).

Anjuran: jalankan cadangan harian (Task Scheduler) dan simpan salinan di luar
mesin yang sama.

---

## 4. Keamanan yang sudah diterapkan

| Area | Implementasi | Lokasi |
|---|---|---|
| Hash kata sandi | argon2-cffi, rehash otomatis | `app/core/security.py` |
| Sesi | token acak 32 byte di DB + cookie HttpOnly/SameSite=Lax | `app/core/deps.py` |
| CSRF | HMAC-SHA256(SECRET_KEY, token_sesi) + double-submit cookie publik | `app/core/security.py` |
| Otorisasi | RBAC per endpoint + cakupan kelas untuk dosen | `app/core/deps.py`, `app/api/dosen.py` |
| SQL injection | parameter binding, whitelist kolom | `app/db/engine.py` |
| XSS | Jinja2 auto-escape + `esc()` pada JS | `templates/`, `static/js/` |
| CSRF pada API | header `X-CSRF-Token` diverifikasi pada router-level | `app/api/admin.py`, `app/api/dosen.py` |
| Rate limit | sliding window: login (8/5 mnt), publik (60/mnt), API (120/mnt) | `app/core/security.py` |
| DoS & muatan besar | `MAX_FRAME_BYTES` 4 MB, sisi gambar dibatasi 800 px | `app/api/public_attendance.py` |
| Beban GPU/CPU | 1 request in-flight per klien, maks 12 wajah/frame, cooldown identitas | `app/face/engine.py` |
| Path traversal | tidak ada endpoint yang membaca path dari pengguna | — |
| Rahasia | `.env` diabaikan git; tidak ada secret di repository | `.gitignore` |
| Privasi | identitas publik disamarkan; frame tidak disimpan; embedding tidak dikirim ke browser | `app/api/public_attendance.py` |
| Audit | setiap mutasi penting tercatat dengan pelaku, IP, dan waktu | `app/db/engine.py::audit` |
| Headers | nosniff, DENY frame, Referrer-Policy, Permissions-Policy camera=(self) | `app/main.py` |

---

## 5. Keterbatasan yang jujur (belum diatasi)

1. **Belum ada liveness / anti-spoofing.** Model yang tersedia (MTCNN +
   InceptionResnetV1 2D) tidak membedakan wajah asli dari foto atau layar
   ponsel. **Sistem ini tidak kebal spoofing.** Mitigasi sementara: aturan
   "absen di tempat", verifikasi dosen, koreksi manual bertrail, dan studi
   liveness terpisah (mis. deteksi kedalaman / blink / challenge-response) bila
   aset dan anggaran tersedia.
2. **Ambang `0.695` memiliki FAR maksimum tinggi.** Laporan riset:
   `FAR_rata 0.94%` tetapi `FAR_maks 30,1%` pada sebagian pasangan, `TAR 75,9%`.
   Artinya ada risiko salah identitas pada kondisi tertentu. Ambang **tidak**
   diturunkan di V2; solusinya: verifikasi admin untuk kasus ambigu.
3. **Skor bukan probabilitas.** Nilai yang ditampilkan adalah cosine similarity,
   bukan confidence terkalibrasi. Jangan diperlakukan sebagai persentase keyakinan.
4. **Rate limit in-memory per proses.** Bila dijalankan beberapa worker
   (uvicorn `--workers > 1`), batas efektifnya menjadi N×. Untuk produksi,
   gunakan reverse proxy dengan rate limiting terpusat.
5. **Enrollment wajah baru sudah persisten.** Embedding hasil enrollment admin
   disimpan di `face_enrollments` (DB V2) dan **dimuat kembali ke galeri aktif
   saat aplikasi start** (`GalleryLoader.merge_db_enrollments`), sehingga identitas
   enrollment tetap dikenali setelah restart. Yang belum ada: enrolling satu
   mahasiswa dengan lebih dari satu foto (multi-embedding) dan reranking.
6. **Tidak ada rekaman video.** Frame hanya diproses di memori lalu dibuang.
7. **Galeri riset berisi 69 identitas.** Identitas di luar itu perlu enrollment
   admin (disarankan minimal 5 foto dengan variasi pencahayaan & posisi).
8. **Tidak ada versioning riwayat untuk koreksi absensi** selain jejak audit
   (nilai lama tidak disimpan terpisah).
9. **Kamera memerlukan HTTPS** di luar `localhost` (keterbatasan browser).
10. **Deteksi wajah kecil/tertutup.** MTCNN dengan `min_face_size=20` dapat
    gagal pada wajah sangat kecil atau tertutup; fallback CLAHE/gamma sudah
    disediakan.

### Cara kerja enrollment wajah

`POST /api/admin/mahasiswa/{id}/wajah` menghitung embedding wajah dengan pipeline
kanonik yang sama dengan galeri, menyimpannya di `face_enrollments` (DB V2),
lalu mendaftarkannya ke galeri aktif di memori proses.

Saat aplikasi start, `FaceEngine._load()` memuat galeri riset (read-only) lalu
memanggil `GalleryLoader.merge_db_enrollments()` untuk menggabungkan seluruh
embedding enrollment `upload` yang tersimpan di DB V2. Dengan demikian identitas
yang di-enroll admin tetap dikenali setelah aplikasi dimulai ulang, tanpa perlu
menyentuh artefak riset.

Embedding enrollment **tidak pernah** ditulis ke `Kapita/gallery.npz`; folder
riset tetap read-only.

---

## 6. Performa terukur di mesin ini

| Konfigurasi | Nilai |
|---|---|
| CPU / GPU | Intel Core (Alder Lake) / NVIDIA MX150, VRAM 2 GB |
| Python / torch | 3.14.8 / 2.14.1+cu126 |
| Muat model (CUDA) | ~14 detik |
| Inferensi 1 wajah | ~0,3–0,6 detik |
| p95 latensi (riset, 1 wajah) | 287 ms |
| Batas wajah per frame | 12 (bisa dikonfigurasi) |
| Deteksi 2 wajah (komposit nyata) | 2/2 terdeteksi, diproses terpisah (terverifikasi) |

Rekomendasi produksi: jalankan dengan GPU bila tersedia (`FACE_DEVICE` kosong →
otomatis CUDA), atau `FACE_DEVICE=cpu` bila GPU dipakai proses lain.

---

## 7. Daftar periksa sebelum produksi

- [ ] `SECRET_KEY` terisi acak dan **tidak** sama dengan V1.
- [ ] `DEBUG=false`, `COOKIE_SECURE=true`, dilayani di belakang HTTPS.
- [ ] `HOST=127.0.0.1` (reverse proxy sebagai satu-satunya pintu masuk).
- [ ] Kata sandi admin dibuat lewat `/setup` (tidak ada akun default).
- [ ] Cadangan harian terjadwal + uji pemulihan.
- [ ] `/api/docs` dinonaktifkan bila tidak diperlukan
      (`app/main.py`: `docs_url=None`).
- [ ] Rate limiting di reverse proxy sebagai lapisan tambahan.
- [ ] Kebijakan privasi & retensi disepakati dengan pihak
      (gambar tidak disimpan; embedding = data biometrik).
- [ ] Modul liveness dievaluasi bila risiko spoofing unacceptable.
