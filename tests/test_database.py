"""Tes skema & integritas database V2."""

from __future__ import annotations

import unittest

from tests import common  # noqa: F401  (bootstrap env)


class TestSkema(unittest.TestCase):
    def setUp(self):
        common.fresh_db()

    def test_semua_tabel_ada(self):
        from app.db.engine import db

        rows = db.query_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        nama = {r["name"] for r in rows}
        wajib = {
            "users", "auth_sessions", "mahasiswa", "dosen", "kelas", "kelas_mahasiswa",
            "kelas_dosen", "face_enrollments", "sesi_absensi", "kehadiran",
            "audit_log", "pengaturan", "schema_version",
        }
        self.assertTrue(wajib.issubset(nama), f"tabel hilang: {wajib - nama}")

    def test_mahasiswa_tidak_memiliki_akun(self):
        """Mahasiswa tidak boleh bisa masuk sebagai user."""
        from app.db.engine import db

        kolom = {r["name"] for r in db.query_all("PRAGMA table_info(users)")}
        self.assertNotIn("nim_mahasiswa", kolom)
        rows = db.query_all("SELECT COUNT(*) AS n FROM users WHERE role='mahasiswa'")
        self.assertEqual(rows[0]["n"], 0)

    def test_unique_kehadiran_per_sesi_mahasiswa(self):
        from app.db.engine import db

        mhs = common.make_mahasiswa("09011182328001", "Mahasiswa Uji", "person-1")
        kelas = common.make_kelas()
        sesi = common.make_sesi(kelas)
        common.add_member(kelas, mhs)

        db.execute(
            """INSERT INTO kehadiran (session_id, class_id, mahasiswa_id, status, similarity,
                                     metode, sumber, recorded_at, updated_at)
               VALUES (?, ?, ?, 'HADIR', 0.9, 'face', 'kamera', '2026-01-01T08:00:00', '2026-01-01T08:00:00')""",
            (sesi, kelas, mhs),
        )
        with self.assertRaises(Exception):
            db.execute(
                """INSERT INTO kehadiran (session_id, class_id, mahasiswa_id, status, similarity,
                                         metode, sumber, recorded_at, updated_at)
                   VALUES (?, ?, ?, 'HADIR', 0.8, 'face', 'kamera', '2026-01-01T08:01:00', '2026-01-01T08:01:00')""",
                (sesi, kelas, mhs),
            )

    def test_cascade_hapus_kelas(self):
        from app.db.engine import db

        mhs = common.make_mahasiswa("09011182328002", "Mahasiswa Dua", "person-2")
        kelas = common.make_kelas()
        sesi = common.make_sesi(kelas)
        common.add_member(kelas, mhs)
        db.execute(
            """INSERT INTO kehadiran (session_id, class_id, mahasiswa_id, status, similarity,
                                     metode, sumber, recorded_at, updated_at)
               VALUES (?, ?, ?, 'HADIR', 0.9, 'face', 'kamera', '2026-01-01T08:00:00', '2026-01-01T08:00:00')""",
            (sesi, kelas, mhs),
        )
        db.execute("DELETE FROM kelas WHERE id = ?", (kelas,))
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM sesi_absensi", (), 0), 0)
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kehadiran", (), 0), 0)
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kelas_mahasiswa", (), 0), 0)
        # mahasiswa tidak ikut terhapus
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM mahasiswa", (), 0), 1)

    def test_nim_mahasiswa_unik(self):
        from app.db.engine import db

        common.make_mahasiswa("09011182328003", "Satu")
        with self.assertRaises(Exception):
            common.make_mahasiswa("09011182328003", "Dua")

    def test_username_admin_unik_case_insensitive(self):
        from app.db.engine import db

        common.make_admin(username="admin")
        with self.assertRaises(Exception):
            common.make_admin(username="ADMIN")

    def test_transaksi_rollback(self):
        from app.db.engine import db

        with self.assertRaises(Exception):
            with db.transaction() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO mahasiswa (nim, nama, aktif, created_at, updated_at)
                       VALUES ('111111', 'Rollback', 1, 'x', 'x')"""
                )
                raise RuntimeError("batal")
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM mahasiswa", (), 0), 0)


if __name__ == "__main__":
    unittest.main()
