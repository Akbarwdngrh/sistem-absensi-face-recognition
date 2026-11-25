# Panduan Penggunaan V2

Panduan singkat untuk admin, dosen, dan mahasiswa.

---

## 1. Mahasiswa (tanpa akun)

1. Buka `http://<server>:8010/absen` (atau `/` yang otomatis mengarah ke sana).
2. Pilih **sesi absensi** yang sedang dibuka pada daftar.
3. Tekan **Mulai Kamera** lalu izinkan akses kamera saat browser memintanya.
4. Berdiri di depan kamera. Kotak hijau = wajah dikenali, kuning = belum dikenali.
   Kotak hanya umpan balik — **tidak perlu mengklik atau menempatkan wajah
   di dalam kotak**.
5. Panel di bawah kamera menampilkan status tiap wajah:
   - **tercatat** — kehadiran tersimpan,
   - **sudah tercatat** — pernah absen pada sesi ini,
   - **tidak dikenal** — wajah tidak cocok dengan galeri,
   - **bukan anggota kelas** — wajah dikenali, tetapi Anda tidak terdaftar di kelas itu.
6. Tekan **Stop** bila selesai. Kamera juga berhenti otomatis saat tab ditutup.

Tips praktis:
- Pencahayaan cukup, wajah menghadap kamera, jarak ±1–2 meter.
- Beberapa orang boleh berdiri bersama dalam satu frame — setiap wajah diproses terpisah.
- Wajah yang tidak dikenali tidak menghasilkan catatan; hubungi dosen untuk
  pencatatan manual (tercatat di audit).

---

## 2. Admin

### Dashboard (`/admin`)
Ringkasan: jumlah mahasiswa/dosen/kelas, sesi terbuka, total kehadiran, koreksi manual,
status model (device, jumlah identitas, ambang), dan 8 aktivitas terakhir.

### Mahasiswa
1. Tab **Mahasiswa** → isi NIM, nama, email (opsional) → **Simpan mahasiswa**.
2. Baris data mahasiswa menampilkan jumlah kelas dan status enrollment wajah.
3. **Daftarkan wajah**: pada kolom terakhir pilih file foto → sistem menghitung
   embedding dengan model yang sama dan menyimpannya di database V2.
   Wajah yang gagal dideteksi akan ditolak dengan pesan.
4. **Anggota kelas**: tab **Kelas** → tombol *Anggota* → pilih mahasiswa → *Tambah*.

### Dosen
1. Tab **Dosen** → isi nama/NID/email.
2. Centang *Buat akun login* bila dosen perlu masuk; isi username + kata sandi
   (min. 10 karakter). Bila tidak dicentang, akun dibuat lewat
   **Reset sandi** (dosen dapat menggantinya sendiri setelah masuk).
3. Penetapan kelas: dari tab **Kelas**, pilih dosen pengampu saat membuat kelas,
   atau lewat endpoint relasi.

### Kelas & Sesi
1. Tab **Kelas** → kode MK, nama MK, label (mis. `SK7A`), dosen pengampu.
2. Tab **Sesi Absensi** → pilih kelas, tanggal, jam mulai (± jam selesai) →
   **Buka sesi**. Sesi yang `buka` langsung tampil di halaman publik mahasiswa.
3. Tombol **Tutup** menutup sesi; pencatatan baru ditolak setelah ditutup.
   Tombol **Buka lagi** membuka kembali (tercatat di audit).

### Kehadiran & Koreksi
1. Tab **Kehadiran** → filter kelas, tanggal, status, atau cari nama/NIM.
2. **Koreksi** untuk mengubah status (HADIR / TOLAK / MANUAL). Alasan wajib diisi
   dan seluruh perubahan tercatat pada log audit beserta pelaku & waktunya.
3. Ekspor **CSV** atau **Excel** untuk filter yang sedang aktif.

### Audit
Tab **Audit** menampilkan setiap aksi penting: login, gagal login, pembuatan akun,
koreksi absensi, hapus data, ekspor, enrollment wajah — lengkap dengan IP.

---

## 3. Dosen

1. Masuk di `/login` memakai akun yang dibuat admin.
2. Dashboard `/dosen` menampilkan:
   - kelas yang Anda ampu (dengan jumlah anggota & sesi),
   - sesi yang sedang dibuka (tombol **Tutup sesi**),
   - rekap kehadiran terbaru **hanya kelas Anda**.
3. Gunakan tombol *Detail* pada kelas untuk melihat daftar mahasiswa beserta
   waktu kehadiran terakhir.
4. Unduh laporan kelas Anda lewat tombol **CSV** / **Excel** pada baris kelas.

Dosen **tidak** dapat melihat atau mengubah kelas lain; pembatasan ditegakkan
di backend (bukan sekadar menu yang disembunyikan).

---

## 4. Endpoint umum untuk integrasi

| Kebutuhan | Endpoint |
|---|---|
| Daftar sesi terbuka | `GET /api/public/sessions` |
| Kirim frame | `POST /api/public/attendance/frame` (multipart: `session_id`, `csrf`, `image`) |
| Status sesi login | `GET /api/auth/me` |
| Status model | `GET /api/auth/engine` |
| Kesehatan aplikasi | `GET /health` |

Semua endpoint admin/dosen memerlukan cookie sesi + header `X-CSRF-Token`
(nilai tersedia di `GET /api/auth/me` → `csrf`).
