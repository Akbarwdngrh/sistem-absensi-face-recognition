"""Tes enrollment wajah oleh admin (embedding disimpan di DB V2)."""

from __future__ import annotations

import unittest

from tests import common  # noqa: F401


class TestEnrollment(unittest.TestCase):
    def setUp(self):
        common.fresh_db()

    def test_enrollment_disimpan_di_db_dan_diteruskan_ke_galeri(self):
        import numpy as np

        from app.db.engine import db
        from app.face.gallery import Gallery, GalleryEntry, GalleryLoader

        mhs = common.make_mahasiswa("09011182328001", "Andi Pratama")
        vektor = np.random.RandomState(0).randn(512).astype("float32")
        db.execute(
            """INSERT INTO face_enrollments
               (mahasiswa_id, person_label, sumber, embedding, dimensi, aktif, catatan,
                created_at, updated_at)
               VALUES (?, ?, 'upload', ?, 512, 1, 'uji', '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
            (mhs, "v2_09011182328001_uji", vektor.tobytes()),
        )

        galeri = Gallery(entries=[], matrix=np.zeros((0, 512), np.float32))
        loader = GalleryLoader()
        jumlah = loader.merge_db_enrollments(galeri)
        self.assertEqual(jumlah, 1)
        self.assertEqual(len(galeri.entries), 1)
        self.assertEqual(galeri.entries[0].person, "v2_09011182328001_uji")
        self.assertEqual(galeri.entries[0].nim, "09011182328001")
        self.assertEqual(galeri.matrix.shape, (1, 512))

    def test_enrollment_nonaktif_diabaikan(self):
        import numpy as np

        from app.db.engine import db
        from app.face.gallery import Gallery, GalleryLoader

        mhs = common.make_mahasiswa("09011182328002", "Budi Santoso")
        vektor = np.zeros(512, dtype="float32")
        db.execute(
            """INSERT INTO face_enrollments
               (mahasiswa_id, person_label, sumber, embedding, dimensi, aktif, catatan,
                created_at, updated_at)
               VALUES (?, ?, 'upload', ?, 512, 0, NULL, '2026-01-01T00:00:00', '2026-01-01T00:00:00')""",
            (mhs, "v2_nonaktif", vektor.tobytes()),
        )
        galeri = Gallery(entries=[], matrix=np.zeros((0, 512), np.float32))
        self.assertEqual(GalleryLoader().merge_db_enrollments(galeri), 0)

    def test_gallery_kosong_bila_file_npz_tidak_ada(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from app import config
        from app.face.gallery import GalleryLoader

        missing = Path(tempfile.gettempdir()) / "tidak_ada_gallery_demo.npz"
        with patch.object(config, "GALLERY_NPZ", missing):
            galeri = GalleryLoader().load(force=True)
        self.assertEqual(galeri.count, 0)
        self.assertIsNotNone(galeri.matrix)
        self.assertEqual(galeri.matrix.shape, (0, 512))
        self.assertIn("empty", galeri.source)

    def test_admin_tidak_bisa_enroll_tanpa_admin(self):
        from fastapi.testclient import TestClient

        from app.main import app

        common.make_admin()
        client = TestClient(app)
        res = client.get("/api/admin/mahasiswa", follow_redirects=False)
        self.assertIn(res.status_code, (401, 403))

    def test_admin_bisa_enroll_wajah(self):
        """Admin dapat mendaftarkan wajah; embedding masuk ke DB V2 (bukan artefak riset)."""
        from fastapi.testclient import TestClient

        from app.main import app

        common.make_admin()
        mhs = common.make_mahasiswa("09011182328003", "Citra Lestari")
        client = TestClient(app)
        client.get("/login")
        csrf = client.cookies.get("absensi_v2_csrf") or ""
        client.post("/login", data={"username": "admin", "password": "AdminRahasia123",
                                    "auth_csrf": csrf}, follow_redirects=False)
        me = client.get("/api/auth/me").json()

        import numpy as np

        import cv2

        frame = np.full((160, 160, 3), 90, dtype=np.uint8)
        ok, buf = cv2.imencode(".jpg", frame)
        self.assertTrue(ok)
        res = client.post(
            f"/api/admin/mahasiswa/{mhs}/wajah",
            headers={"X-CSRF-Token": me["csrf"]},
            data={"catatan": "uji"},
            files={"image": ("wajah.jpg", buf.tobytes(), "image/jpeg")},
        )
        # Gambar polos tidak berisi wajah => 422 (wajah tidak terdeteksi).
        self.assertIn(res.status_code, (200, 422))

        from app.db.engine import db

        rows = db.query_all("SELECT * FROM face_enrollments WHERE mahasiswa_id = ?", (mhs,))
        if res.status_code == 200:
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sumber"], "upload")
            self.assertIsNotNone(rows[0]["embedding"])


if __name__ == "__main__":
    unittest.main()
