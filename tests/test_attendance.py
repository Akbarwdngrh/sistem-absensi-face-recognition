"""Tes alur absensi: validasi sesi, keanggotaan, duplikasi, dan multi-face."""

from __future__ import annotations

import unittest
from unittest import mock

from tests import common  # noqa: F401


class TestAbsensi(unittest.TestCase):
    def setUp(self):
        common.fresh_db()
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)
        common.make_admin()
        self.mhs_a = common.make_mahasiswa("09011182328001", "Andi Pratama", "person-andi")
        self.mhs_b = common.make_mahasiswa("09011182328002", "Budi Santoso", "person-budi")
        self.mhs_luar = common.make_mahasiswa("09011182328003", "Citra Lestari", "person-citra")
        self.kelas = common.make_kelas()
        common.add_member(self.kelas, self.mhs_a)
        common.add_member(self.kelas, self.mhs_b)
        self.sesi = common.make_sesi(self.kelas)

    # -- helper ----------------------------------------------------------
    def _csrf(self) -> str:
        self.client.get("/absen")
        return self.client.cookies.get("absensi_v2_csrf") or ""

    def _kirim(self, session_id: int | None = None, csrf: str | None = None):
        """Kirim frame (JPEG valid) ke endpoint absensi publik."""
        csrf = csrf if csrf is not None else self._csrf()
        import numpy as np

        import cv2

        frame = np.full((240, 320, 3), 128, dtype=np.uint8)
        ok, buf = cv2.imencode(".jpg", frame)
        self.assertTrue(ok)
        data = {"session_id": str(session_id or self.sesi), "csrf": csrf}
        return self.client.post(
            "/api/public/attendance/frame",
            data=data,
            files={"image": ("frame.jpg", buf.tobytes(), "image/jpeg")},
        )

    def _patch_engine(self, faces):
        return mock.patch(
            "app.api.public_attendance.engine.recognize_all",
            return_value={"ok": True, "det_status": "mtcnn", "n_faces": len(faces),
                          "n_detected": len(faces), "faces": faces, "latency_ms": 12.0},
        )

    @staticmethod
    def _wajah(person: str, similarity: float = 0.85, index: int = 0, box=None):
        return {
            "index": index,
            "box": box or [0.0, 0.0, 100.0, 100.0],
            "similarity": similarity,
            "person": person,
            "nama": "Tester",
            "nim": "",
            "kelas": "",
            "recognized": similarity >= 0.695,
            "reason": "match" if similarity >= 0.695 else "below_threshold",
            "det_status": "mtcnn",
            "enrolled": True,
        }

    # -- pengujian -------------------------------------------------------
    def test_sesi_tertutup_menolak_pencatatan(self):
        from app.db.engine import db

        db.execute("UPDATE sesi_absensi SET status='tutup' WHERE id=?", (self.sesi,))
        with self._patch_engine([self._wajah("person-andi")]):
            res = self._kirim()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["reason"], "session_closed")

    def test_sesi_tidak_ada(self):
        with self._patch_engine([]):
            res = self._kirim(session_id=999999)
        self.assertEqual(res.status_code, 404)

    def test_wajah_tidak_dikenal_tidak_mencatat(self):
        from app.db.engine import db

        with self._patch_engine([self._wajah("", similarity=0.31)]):
            res = self._kirim()
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["n_tercatat"], 0)
        self.assertEqual(data["faces"][0]["status"], "tidak_dikenal")
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kehadiran", (), 0), 0)

    def test_wajah_dikenal_anggota_kelas_tercatat(self):
        from app.db.engine import db

        with self._patch_engine([self._wajah("person-andi")]):
            res = self._kirim()
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["n_tercatat"], 1)
        self.assertEqual(data["faces"][0]["status"], "tercatat")
        row = db.query_one("SELECT * FROM kehadiran WHERE session_id=?", (self.sesi,))
        self.assertIsNotNone(row)
        self.assertEqual(row["mahasiswa_id"], self.mhs_a)
        self.assertEqual(row["status"], "HADIR")
        self.assertEqual(row["metode"], "face")
        self.assertEqual(row["sumber"], "kamera")
        self.assertAlmostEqual(row["similarity"], 0.85, places=3)

    def test_wajah_dikenal_tapi_bukan_anggota_tidak_mencatat(self):
        from app.db.engine import db

        with self._patch_engine([self._wajah("person-citra")]):
            res = self._kirim()
        data = res.json()
        self.assertEqual(data["n_tercatat"], 0)
        self.assertEqual(data["faces"][0]["status"], "tidak_anggota")
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kehadiran", (), 0), 0)

    def test_duplikasi_tidak_membuat_catatan_kedua(self):
        from app.db.engine import db

        with self._patch_engine([self._wajah("person-andi")]):
            self._kirim()
        with self._patch_engine([self._wajah("person-andi")]):
            res = self._kirim()
        data = res.json()
        self.assertEqual(data["n_tercatat"], 0)
        self.assertEqual(data["faces"][0]["status"], "sudah_tercatat")
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kehadiran", (), 0), 1)

    def test_multi_wajah_mencatat_beberapa_mahasiswa(self):
        from app.db.engine import db

        faces = [
            self._wajah("person-andi", index=0),
            self._wajah("person-budi", index=1),
        ]
        with self._patch_engine(faces):
            res = self._kirim()
        data = res.json()
        self.assertEqual(data["n_wajah"], 2)
        self.assertEqual(data["n_tercatat"], 2)
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kehadiran", (), 0), 2)
        mhs = {r["mahasiswa_id"] for r in db.query_all("SELECT mahasiswa_id FROM kehadiran")}
        self.assertEqual(mhs, {self.mhs_a, self.mhs_b})

    def test_wajah_sama_dua_kali_dalam_frame_tetap_satu_catatan(self):
        from app.db.engine import db

        faces = [
            self._wajah("person-andi", index=0),
            self._wajah("person-andi", index=1),
        ]
        with self._patch_engine(faces):
            res = self._kirim()
        data = res.json()
        self.assertEqual(data["n_wajah"], 2)
        self.assertEqual(db.query_value("SELECT COUNT(*) FROM kehadiran", (), 0), 1)
        status = sorted(f["status"] for f in data["faces"])
        self.assertEqual(status, ["sudah_tercatat", "tercatat"])

    def test_csrf_publik_wajib(self):
        with self._patch_engine([self._wajah("person-andi")]):
            res = self._kirim(csrf="token-palsu")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["reason"], "csrf")

    def test_identitas_publik_disamarkan(self):
        with self._patch_engine([self._wajah("person-andi")]):
            res = self._kirim()
        face = res.json()["faces"][0]
        self.assertNotEqual(face["nama"], "Andi Pratama")
        self.assertIn("*", face["nim"])

    def test_sesi_buka_ditampilkan_publik(self):
        res = self.client.get("/api/public/sessions")
        self.assertEqual(res.status_code, 200)
        sessions = res.json()["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], self.sesi)
        # Halaman publik tidak boleh membocorkan identitas mahasiswa.
        isi = str(sessions[0]).lower()
        self.assertNotIn("andi", isi)
        self.assertNotIn("budi", isi)
        self.assertNotIn("09011182328001", isi)
        self.assertIn("n_anggota", sessions[0])


if __name__ == "__main__":
    unittest.main()
