"""Tes alur autentikasi & otorisasi berbasis peran (RBAC)."""

from __future__ import annotations

import unittest

from tests import common  # noqa: F401


class TestAlurAuth(unittest.TestCase):
    def setUp(self):
        common.fresh_db()
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)
        self.app = app

    # -- helper ----------------------------------------------------------
    def _csrf_publik(self) -> str:
        self.client.get("/login")
        return self.client.cookies.get("absensi_v2_csrf") or ""

    def _login(self, username: str, password: str):
        token = self._csrf_publik()
        return self.client.post(
            "/login",
            data={"username": username, "password": password, "auth_csrf": token},
            follow_redirects=False,
        )

    def _csrf_terautentikasi(self) -> str:
        from app.core import security
        from app.core.deps import get_secret

        session_token = self.client.cookies.get("absensi_v2_session") or ""
        return security.csrf_for(get_secret(), session_token)

    # -- setup admin ------------------------------------------------------
    def test_setup_membuat_admin_pertama(self):
        token = self.client.get("/setup").cookies.get("absensi_v2_csrf") or ""
        res = self.client.post(
            "/setup",
            data={
                "nama": "Admin Pertama",
                "username": "admin",
                "password": "AdminRahasia123",
                "password2": "AdminRahasia123",
                "auth_csrf": token,
            },
            follow_redirects=False,
        )
        self.assertEqual(res.status_code, 303)
        from app.db.engine import db

        row = db.query_one("SELECT role FROM users WHERE username='admin'")
        self.assertIsNotNone(row)
        self.assertEqual(row["role"], "admin")

    def test_setup_ditolak_setelah_admin_ada(self):
        common.make_admin()
        res = self.client.get("/setup", follow_redirects=False)
        self.assertEqual(res.status_code, 303)

    def test_halaman_terproteksi_mengalihkan_ke_setup(self):
        res = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertIn("/setup", res.headers.get("location", ""))

    # -- login ------------------------------------------------------------
    def test_login_admin_berhasil(self):
        common.make_admin()
        res = self._login("admin", "AdminRahasia123")
        self.assertEqual(res.status_code, 303)
        self.assertIn("/admin", res.headers.get("location", ""))

    def test_login_salah_ditolak(self):
        common.make_admin()
        res = self._login("admin", "SalahSekali123")
        self.assertEqual(res.status_code, 303)
        self.assertIn("/login", res.headers.get("location", ""))

    def test_login_tanpa_csrf_ditolak(self):
        common.make_admin()
        res = self.client.post(
            "/login",
            data={"username": "admin", "password": "AdminRahasia123", "auth_csrf": "palsu"},
            follow_redirects=False,
        )
        self.assertIn("/login", res.headers.get("location", ""))
        self.assertIn("err", res.headers.get("location", ""))

    def test_logout_mengakhiri_sesi(self):
        common.make_admin()
        self._login("admin", "AdminRahasia123")
        res = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        res2 = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(res2.status_code, 303)
        self.assertIn("/login", res2.headers.get("location", ""))

    # -- RBAC -------------------------------------------------------------
    def test_admin_boleh_buka_dashboard_admin(self):
        common.make_admin()
        self._login("admin", "AdminRahasia123")
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 200)

    def test_dosen_tidak_boleh_buka_api_admin(self):
        common.make_admin()
        common.make_dosen()
        self._login("dosen", "DosenRahasia123")
        res = self.client.get("/api/admin/mahasiswa", follow_redirects=False)
        self.assertEqual(res.status_code, 403)

    def test_dosen_tidak_boleh_buka_halaman_admin(self):
        common.make_admin()
        common.make_dosen()
        self._login("dosen", "DosenRahasia123")
        res = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(res.status_code, 403)

    def test_tamu_tidak_boleh_buka_api_admin(self):
        common.make_admin()
        res = self.client.get("/api/admin/mahasiswa", follow_redirects=False)
        self.assertIn(res.status_code, (401, 403))

    def test_api_admin_menolak_tanpa_csrf(self):
        common.make_admin()
        self._login("admin", "AdminRahasia123")
        res = self.client.post("/api/admin/mahasiswa", json={"nim": "123456", "nama": "X"})
        self.assertEqual(res.status_code, 403)

    def test_api_admin_menerima_dengan_csrf(self):
        common.make_admin()
        self._login("admin", "AdminRahasia123")
        res = self.client.post(
            "/api/admin/mahasiswa",
            json={"nim": "09011182328999", "nama": "Uji Coba"},
            headers={"X-CSRF-Token": self._csrf_terautentikasi()},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["ok"])

    def test_dashboard_dosen_terbuka_untuk_dosen(self):
        common.make_admin()
        common.make_dosen()
        self._login("dosen", "DosenRahasia123")
        res = self.client.get("/dosen")
        self.assertEqual(res.status_code, 200)

    def test_api_me_menampilkan_peran(self):
        common.make_admin()
        common.make_dosen()
        self._login("dosen", "DosenRahasia123")
        data = self.client.get("/api/auth/me").json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["role"], "dosen")

    def test_api_admin_menolak_nim_duplikat(self):
        common.make_admin()
        self._login("admin", "AdminRahasia123")
        headers = {"X-CSRF-Token": self._csrf_terautentikasi()}
        first = self.client.post("/api/admin/mahasiswa",
                                 json={"nim": "09011182328777", "nama": "Pertama"},
                                 headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post("/api/admin/mahasiswa",
                                  json={"nim": "09011182328777", "nama": "Kedua"},
                                  headers=headers)
        self.assertEqual(second.status_code, 409)


if __name__ == "__main__":
    unittest.main()
