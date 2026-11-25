"""Tes keamanan: kata sandi, CSRF, rate limit, mask identitas."""

from __future__ import annotations

import unittest

from tests import common  # noqa: F401


class TestKataSandi(unittest.TestCase):
    def test_kebijakan_minimal(self):
        from app.core import security

        self.assertIsNotNone(security.validate_password("abc", 10))
        self.assertIsNone(security.validate_password("RahasiaKuat123", 10))

    def test_tolak_sama_dengan_username(self):
        from app.core import security

        err = security.validate_password("admin12345", 10, username="admin12345")
        self.assertIsNotNone(err)

    def test_tolak_karakter_sama(self):
        from app.core import security

        self.assertIsNotNone(security.validate_password("aaaaaaaaaaaa", 10))

    def test_hash_dan_verifikasi(self):
        from app.core import security

        h = security.hash_password("RahasiaKuat123")
        self.assertTrue(h.startswith("$argon2"))
        self.assertTrue(security.verify_password("RahasiaKuat123", h))
        self.assertFalse(security.verify_password("SalahSekali", h))

    def test_verify_tidak_lemah_pada_hash_aneh(self):
        from app.core import security

        self.assertFalse(security.verify_password("apa saja", "bukan-hash"))


class TestCSRF(unittest.TestCase):
    def test_token_deterministik_dan_unik_per_sesi(self):
        from app.core import security

        a = security.csrf_for("kunci", "sesi-1")
        b = security.csrf_for("kunci", "sesi-1")
        c = security.csrf_for("kunci", "sesi-2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_pencocokan_menolak_kunci_salah(self):
        from app.core import security

        self.assertTrue(security.csrf_matches("kunci", "sesi-1", security.csrf_for("kunci", "sesi-1")))
        self.assertFalse(security.csrf_matches("kunci", "sesi-1", security.csrf_for("lain", "sesi-1")))
        self.assertFalse(security.csrf_matches("kunci", "sesi-1", ""))
        self.assertFalse(security.csrf_matches("kunci", "", "apa saja"))


class TestRateLimit(unittest.TestCase):
    def test_batas_dilewati(self):
        from app.core.security import RateLimiter

        rl = RateLimiter()
        self.assertTrue(rl.allow("kunci", 3, 60))
        self.assertTrue(rl.allow("kunci", 3, 60))
        self.assertTrue(rl.allow("kunci", 3, 60))
        self.assertFalse(rl.allow("kunci", 3, 60))

    def test_kunci_terpisah(self):
        from app.core.security import RateLimiter

        rl = RateLimiter()
        rl.allow("a", 1, 60)
        self.assertTrue(rl.allow("b", 1, 60))


class TestMasking(unittest.TestCase):
    def test_nama_dimasking(self):
        from app.api.public_attendance import mask_name, mask_nim

        self.assertEqual(mask_name("Budi Santoso"), "Budi S.")
        self.assertEqual(mask_name("Siti"), "S.")
        self.assertEqual(mask_name(""), "Mahasiswa")
        self.assertEqual(mask_nim("09011182328001"), "**********8001")

    def test_nim_kosong(self):
        from app.api.public_attendance import mask_nim

        self.assertEqual(mask_nim(""), "")


if __name__ == "__main__":
    unittest.main()
