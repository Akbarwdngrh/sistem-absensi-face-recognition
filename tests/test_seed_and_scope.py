"""Tes skrip operasional: impor roster & pembatasan akses dosen."""

from __future__ import annotations

import unittest

from tests import common  # noqa: F401


class TestSeedRoster(unittest.TestCase):
    def setUp(self):
        common.fresh_db()

    def test_dry_run_tidak_menulis(self):
        from scripts import seed_from_roster

        before = common.fresh_db()
        self.assertIsNone(before)
        rc = seed_from_roster.main(["--dry-run"])
        self.assertEqual(rc, 0)
        from app.db.engine import db

        self.assertEqual(db.query_value("SELECT COUNT(*) FROM mahasiswa", (), 0), 0)

    def test_import_roster_membuat_data_dan_idempoten(self):
        from app.db.engine import db
        from scripts import seed_from_roster

        rc = seed_from_roster.main([])
        self.assertEqual(rc, 0)
        mhs = db.query_value("SELECT COUNT(*) FROM mahasiswa", (), 0)
        kelas = db.query_value("SELECT COUNT(*) FROM kelas", (), 0)
        enrollment = db.query_value("SELECT COUNT(*) FROM face_enrollments", (), 0)
        self.assertGreater(mhs, 0)
        self.assertGreater(kelas, 0)
        self.assertEqual(enrollment, mhs, "Setiap mahasiswa harus punya enrollment biometrik")

        # Jalankan kedua kali: tidak boleh menggandakan data.
        seed_from_roster.main([])
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM mahasiswa", (), 0), mhs)
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kelas", (), 0), kelas)
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM face_enrollments", (), 0), enrollment)

    def test_seed_tidak_membuat_akun_login(self):
        from app.db.engine import db
        from scripts import seed_from_roster

        seed_from_roster.main([])
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM users", (), 0), 0)


class TestCakupanDosen(unittest.TestCase):
    """Dosen hanya boleh melihat kelas yang diampu."""

    def setUp(self):
        common.fresh_db()
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)
        common.make_admin()
        self.dosen_id, self.user_id = common.make_dosen()
        self.kelas_mera = common.make_kelas(label="SK7A", kode="SK7A")
        self.kelas_lain = common.make_kelas(label="SKU7A", kode="SKU7A")
        # Dosen hanya mengampu kelas pertama.
        from app.db.engine import db, now_iso

        db.execute(
            "INSERT INTO kelas_dosen (class_id, dosen_id, created_at) VALUES (?, ?, ?)",
            (self.kelas_mera, self.dosen_id, now_iso()),
        )
        self.mhs = common.make_mahasiswa("09011182328001", "Andi Pratama", "person-andi")
        common.add_member(self.kelas_mera, self.mhs)
        self.sesi = common.make_sesi(self.kelas_mera)

        from app.core import security

        ts = now_iso()
        db.execute(
            """INSERT INTO kehadiran (session_id, class_id, mahasiswa_id, status, similarity,
                                     metode, sumber, recorded_at, updated_at)
               VALUES (?, ?, ?, 'HADIR', 0.91, 'face', 'kamera', '2026-01-01T08:00:00', ?)""",
            (self.sesi, self.kelas_mera, self.mhs, ts),
        )

    def _login(self):
        self.client.get("/login")
        token = self.client.cookies.get("absensi_v2_csrf") or ""
        self.client.post("/login", data={
            "username": "dosen", "password": "DosenRahasia123", "auth_csrf": token,
        }, follow_redirects=False)

    def test_dosen_melihat_kelas_sendiri(self):
        self._login()
        data = self.client.get("/api/dosen/ringkasan").json()
        self.assertEqual(data["n_kelas"], 1)
        self.assertEqual(data["kelas"][0]["id"], self.kelas_mera)

    def test_dosen_tidak_bisa_akses_kelas_lain(self):
        self._login()
        res = self.client.get(f"/api/dosen/kelas/{self.kelas_lain}/kehadiran")
        self.assertEqual(res.status_code, 403)

    def test_dosen_tidak_bisa_membuka_api_admin(self):
        self._login()
        self.assertEqual(self.client.get("/api/admin/kehadiran").status_code, 403)

    def test_rekap_dosen_terbatas_kelasnya(self):
        self._login()
        data = self.client.get("/api/dosen/kehadiran").json()
        for row in data["rows"]:
            self.assertEqual(row["class_id"], self.kelas_mera)

    def test_admin_melihat_semua_kelas(self):
        self.client.get("/login")
        token = self.client.cookies.get("absensi_v2_csrf") or ""
        self.client.post("/login", data={
            "username": "admin", "password": "AdminRahasia123", "auth_csrf": token,
        }, follow_redirects=False)
        data = self.client.get("/api/admin/kehadiran").json()
        self.assertGreaterEqual(data["total"], 1)


class TestEkspor(unittest.TestCase):
    def setUp(self):
        common.fresh_db()

    def test_ekspor_csv_dan_xlsx(self):
        from app.db.engine import db
        from app.services import laporan

        mhs = common.make_mahasiswa("09011182328001", "Andi Pratama", "person-andi")
        kelas = common.make_kelas()
        sesi = common.make_sesi(kelas)
        common.add_member(kelas, mhs)
        db.execute(
            """INSERT INTO kehadiran (session_id, class_id, mahasiswa_id, status, similarity,
                                     metode, sumber, recorded_at, updated_at)
               VALUES (?, ?, ?, 'HADIR', 0.9123, 'face', 'kamera', '2026-01-01T08:00:00', '2026-01-01T08:00:00')""",
            (sesi, kelas, mhs),
        )
        rows, total = laporan.rekap()
        self.assertEqual(total, 1)

        csv_path = laporan.export_records(rows, fmt="csv", prefix="uji")
        self.assertTrue(csv_path.exists())
        isi = csv_path.read_text(encoding="utf-8-sig")
        self.assertIn("Andi Pratama", isi)
        self.assertIn("0.9123", isi)

        xlsx_path = laporan.export_records(rows, fmt="xlsx", prefix="uji")
        self.assertTrue(xlsx_path.exists())

    def test_sanitasi_formula_injection(self):
        from app.services.laporan import sanitize_cell

        self.assertEqual(sanitize_cell("=SUM(A1)"), "'=SUM(A1)")
        self.assertEqual(sanitize_cell("+1"), "'+1")
        self.assertEqual(sanitize_cell("-1"), "'-1")
        self.assertEqual(sanitize_cell("@x"), "'@x")
        self.assertEqual(sanitize_cell("biasa"), "biasa")


if __name__ == "__main__":
    unittest.main()
