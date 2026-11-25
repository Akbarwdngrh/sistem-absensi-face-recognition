"""Skema database V2.

Terpisah total dari database V1 (`Dev/data/absensi.db`). Skema ditulis dengan
SQL portabel: menghindari AUTOINCREMENT/AUTOINCREMENT khusus SQLite, memakai
`INTEGER PRIMARY KEY` yang dipetakan otomatis ke BIGSERIAL/IDENTITY di
PostgreSQL, dan `ON CONFLICT DO NOTHING` yang didukung kedua mesin.

Entitas V2 memisahkan akun (admin/dosen) dari data mahasiswa, karena mahasiswa
pada V2 TIDAK memiliki akun login.

Konvensi:
  * id        : INTEGER PRIMARY KEY (dibuat otomatis)
  * created_at: TEXT ISO-8601 (portabel lintas mesin)
  * status    : TEXT dengan CHECK (bukan ENUM, agar migrasi mudah)
"""

from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA = """
-- ---------------------------------------------------------------- akun ----
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin','dosen')),
    nama          TEXT    NOT NULL,
    email         TEXT,
    nim           TEXT,
    aktif         INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users(lower(username));
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nim_unique
    ON users(nim) WHERE nim IS NOT NULL AND nim <> '';
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- token sesi login (admin & dosen)
CREATE TABLE IF NOT EXISTS auth_sessions (
    token         TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL,
    expires_at    TEXT    NOT NULL,
    user_agent    TEXT,
    ip            TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_user ON auth_sessions(user_id);

-- ---------------------------------------------------------- mahasiswa ----
CREATE TABLE IF NOT EXISTS mahasiswa (
    id            INTEGER PRIMARY KEY,
    nim           TEXT    NOT NULL,
    nama          TEXT    NOT NULL,
    email         TEXT,
    no_hp         TEXT,
    aktif         INTEGER NOT NULL DEFAULT 1,
    dibuat_oleh   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mahasiswa_nim ON mahasiswa(lower(nim));
CREATE INDEX IF NOT EXISTS idx_mahasiswa_nama ON mahasiswa(nama);

-- -------------------------------------------------------------- dosen ----
CREATE TABLE IF NOT EXISTS dosen (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    nid           TEXT,
    nama          TEXT    NOT NULL,
    email         TEXT,
    no_hp         TEXT,
    aktif         INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dosen_user ON dosen(user_id);

-- -------------------------------------------------------------- kelas ----
CREATE TABLE IF NOT EXISTS kelas (
    id            INTEGER PRIMARY KEY,
    kode_mk       TEXT    NOT NULL,
    nama_mk       TEXT    NOT NULL,
    label         TEXT    NOT NULL,
    dosen_id      INTEGER REFERENCES dosen(id) ON DELETE SET NULL,
    tahun_akademik TEXT,
    semester      TEXT,
    status        TEXT    NOT NULL DEFAULT 'aktif' CHECK (status IN ('aktif','nonaktif')),
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE (kode_mk, label)
);
CREATE INDEX IF NOT EXISTS idx_kelas_dosen ON kelas(dosen_id);

-- anggota kelas (mahasiswa <-> kelas)
CREATE TABLE IF NOT EXISTS kelas_mahasiswa (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES kelas(id) ON DELETE CASCADE,
    mahasiswa_id  INTEGER NOT NULL REFERENCES mahasiswa(id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL,
    UNIQUE (class_id, mahasiswa_id)
);
CREATE INDEX IF NOT EXISTS idx_km_mahasiswa ON kelas_mahasiswa(mahasiswa_id);

-- dosen pengampu kelas (dosen boleh mengampu lebih dari satu kelas)
CREATE TABLE IF NOT EXISTS kelas_dosen (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES kelas(id) ON DELETE CASCADE,
    dosen_id      INTEGER NOT NULL REFERENCES dosen(id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL,
    UNIQUE (class_id, dosen_id)
);
CREATE INDEX IF NOT EXISTS idx_kd_dosen ON kelas_dosen(dosen_id);

-- --------------------------------------------- referensi wajah/biometrik --
-- `person_label` adalah kunci yang sama dengan gallery_meta.json / gallery.npz.
-- Kolom `embedding` (BLOB float32, 512-d) dipakai untuk enrollment baru di V2.
CREATE TABLE IF NOT EXISTS face_enrollments (
    id            INTEGER PRIMARY KEY,
    mahasiswa_id  INTEGER NOT NULL REFERENCES mahasiswa(id) ON DELETE CASCADE,
    person_label  TEXT    NOT NULL,
    sumber        TEXT    NOT NULL DEFAULT 'gallery'
                 CHECK (sumber IN ('gallery','upload','manual')),
    embedding     BLOB,
    dimensi       INTEGER,
    aktif         INTEGER NOT NULL DEFAULT 1,
    catatan       TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE (mahasiswa_id, person_label)
);
CREATE INDEX IF NOT EXISTS idx_face_person ON face_enrollments(person_label);
CREATE INDEX IF NOT EXISTS idx_face_mahasiswa ON face_enrollments(mahasiswa_id);

-- -------------------------------------------------------- sesi absensi ----
CREATE TABLE IF NOT EXISTS sesi_absensi (
    id            INTEGER PRIMARY KEY,
    class_id      INTEGER NOT NULL REFERENCES kelas(id) ON DELETE CASCADE,
    tanggal       TEXT    NOT NULL,
    mulai_at      TEXT    NOT NULL,
    selesai_at    TEXT,
    status        TEXT    NOT NULL DEFAULT 'buka' CHECK (status IN ('buka','tutup')),
    keterangan    TEXT,
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE (class_id, tanggal, mulai_at)
);
CREATE INDEX IF NOT EXISTS idx_sesi_class ON sesi_absensi(class_id);
CREATE INDEX IF NOT EXISTS idx_sesi_status ON sesi_absensi(status);

-- ------------------------------------------------------ catatan kehadiran --
CREATE TABLE IF NOT EXISTS kehadiran (
    id             INTEGER PRIMARY KEY,
    session_id     INTEGER NOT NULL REFERENCES sesi_absensi(id) ON DELETE CASCADE,
    class_id       INTEGER NOT NULL REFERENCES kelas(id) ON DELETE CASCADE,
    mahasiswa_id   INTEGER NOT NULL REFERENCES mahasiswa(id) ON DELETE CASCADE,
    status         TEXT    NOT NULL DEFAULT 'HADIR'
                   CHECK (status IN ('HADIR','TOLAK','MANUAL')),
    similarity     REAL,
    metode         TEXT    NOT NULL DEFAULT 'face' CHECK (metode IN ('face','manual')),
    sumber         TEXT    NOT NULL DEFAULT 'kamera' CHECK (sumber IN ('kamera','manual','impor')),
    det_status     TEXT,
    n_wajah        INTEGER,
    bbox           TEXT,
    catatan        TEXT,
    recorded_at    TEXT    NOT NULL,
    created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at     TEXT    NOT NULL,
    UNIQUE (session_id, mahasiswa_id)
);
CREATE INDEX IF NOT EXISTS idx_hadir_sesi ON kehadiran(session_id);
CREATE INDEX IF NOT EXISTS idx_hadir_kelas ON kehadiran(class_id);
CREATE INDEX IF NOT EXISTS idx_hadir_mahasiswa ON kehadiran(mahasiswa_id);
CREATE INDEX IF NOT EXISTS idx_hadir_waktu ON kehadiran(recorded_at);

-- ------------------------------------------------------------- audit ----
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    aksi          TEXT    NOT NULL,
    entitas       TEXT,
    entitas_id    INTEGER,
    detail        TEXT,
    ip            TEXT,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_aksi ON audit_log(aksi);
CREATE INDEX IF NOT EXISTS idx_audit_waktu ON audit_log(created_at);

-- --------------------------------------------- pengaturan sistem ----
CREATE TABLE IF NOT EXISTS pengaturan (
    key           TEXT PRIMARY KEY,
    value         TEXT,
    updated_at    TEXT    NOT NULL
);

-- tabel versi skema (migrasi)
CREATE TABLE IF NOT EXISTS schema_version (
    version       INTEGER NOT NULL,
    applied_at    TEXT    NOT NULL
);
"""
