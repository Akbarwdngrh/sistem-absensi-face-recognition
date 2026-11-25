"""Tes integrasi multi-wajah dengan model & galeri ASLI (read-only).

Tes ini memuat model sungguhan (torch + facenet-pytorch) dan membaca artefak
riset secara read-only. Dilewati otomatis bila artefak tidak tersedia.

Cakupan:
  1. Kompatibilitas pipeline  : hasil V2 untuk wajah tunggal = hasil V1.
  2. Multi-face detection     : dua wajah dalam satu frame terdeteksi keduanya.
  3. Multi-face recognition   : tiap wajah dicocokkan secara independen.
  4. Multi-face attendance    : dua mahasiswa berbeda tercatat terpisah.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests import common  # noqa: F401

KAPITA = Path(os.environ.get("KAPITA_ROOT", r"C:\Users\Acer\Documents\Kapita"))
GALLERY = KAPITA / "gallery.npz"
MODEL = Path(r"C:\Users\Acer\Documents\kapitaselekta\model\inception_resnet_v1_vggface2.pt")


def _cari_foto(indeks: int) -> Path | None:
    """Ambil foto wajah ke-N dari folder dataset Kapita (read-only)."""
    if not KAPITA.exists():
        return None
    foto: list[Path] = []
    for kelas in ("SK7A", "SKU7A"):
        folder = KAPITA / kelas
        if not folder.exists():
            continue
        for orang in sorted(folder.iterdir()):
            if not orang.is_dir():
                continue
            for f in sorted(orang.iterdir()):
                if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    foto.append(f)
                    break
    if len(foto) <= indeks:
        return None
    return foto[indeks]


def _kirim_bytes(path: Path) -> bytes:
    return path.read_bytes()


@unittest.skipUnless(GALLERY.exists() and MODEL.exists(),
                     "Artefak model/galeri tidak tersedia — tes integrasi dilewati.")
class TestMultiFaceNyata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.face.engine import engine

        if not engine.ensure_loaded():
            raise unittest.SkipTest(f"Mesin wajah tidak siap: {engine.status.error}")

    def setUp(self):
        common.fresh_db()

    # -- 1. kompatibilitas dengan V1 --------------------------------------
    def test_pipeline_v2_kompatibel_dengan_v1(self):
        """Wajah tunggal: identitas & skor V2 harus sama dengan V1."""
        import sys

        from app.face.engine import engine
        from app.face.pipeline import imread_bytes

        foto = _cari_foto(0)
        if foto is None:
            self.skipTest("Foto dataset tidak ditemukan")
        bgr = imread_bytes(_kirim_bytes(foto))
        self.assertIsNotNone(bgr)

        hasil_v2 = engine.recognize_all(bgr)
        self.assertEqual(hasil_v2["n_faces"], 1)
        face = hasil_v2["faces"][0]

        v1_dir = Path(r"C:\Users\Acer\Documents\kapitaselekta\Dev")
        if not (v1_dir / "inference.py").exists():
            self.skipTest("V1 tidak tersedia di mesin ini — perbandingan dilewati")

        for mod in ("inference", "config"):
            sys.modules.pop(mod, None)
        sys.path.insert(0, str(v1_dir))
        try:
            import inference as v1_inference  # type: ignore

            hasil_v1 = v1_inference.engine.recognize(bgr)
        finally:
            sys.path.remove(str(v1_dir))
            sys.modules.pop("inference", None)
            sys.modules.pop("config", None)

        if hasil_v1.get("dikenali"):
            self.assertTrue(face["recognized"], "V2 gagal mengenali wajah yang dikenali V1")
            self.assertEqual(face["person"], hasil_v1["person"])
            self.assertAlmostEqual(face["similarity"], hasil_v1["similarity"], places=4)
        else:
            self.assertFalse(face["recognized"])

    # -- 2 & 3. deteksi + pengenalan multi-wajah ---------------------------
    def test_dua_wajah_dalam_satu_frame(self):
        import cv2
        import numpy as np

        from app.face.engine import engine
        from app.face.pipeline import imread_bytes

        foto_a = _cari_foto(0)
        foto_b = _cari_foto(1)
        if foto_a is None or foto_b is None:
            self.skipTest("Foto dataset tidak cukup")
        a = imread_bytes(_kirim_bytes(foto_a))
        b = imread_bytes(_kirim_bytes(foto_b))
        if a is None or b is None:
            self.skipTest("Foto tidak terbaca")

        def kecil(img):
            return cv2.resize(img, (360, 360))

        komposit = np.hstack([kecil(a), kecil(b)])
        hasil = engine.recognize_all(komposit)

        self.assertGreaterEqual(hasil["n_faces"], 2,
                                f"Hanya {hasil['n_faces']} wajah terdeteksi")
        # Kotak wajah harus berbeda (dua individu, bukan satu yang diduplikasi).
        boxes = [f["box"] for f in hasil["faces"][:2]]
        self.assertNotEqual(boxes[0], boxes[1])

        # Setiap wajah diproses independen: similarity & status sendiri.
        for face in hasil["faces"]:
            self.assertIn("similarity", face)
            self.assertIn("recognized", face)
            self.assertGreaterEqual(face["similarity"], 0.0)
            self.assertLessEqual(face["similarity"], 1.0001)

        # Setidaknya satu wajah harus dikenali dari galeri.
        dikenali = [f for f in hasil["faces"] if f["recognized"]]
        self.assertTrue(dikenali, "Tidak ada wajah yang dikenali dari komposit")

    # -- 4. multi-face attendance -----------------------------------------
    def test_pencatatan_dua_mahasiswa_dalam_satu_frame(self):
        import cv2
        import numpy as np

        from app.face.engine import engine
        from app.face.pipeline import imread_bytes
        from app.services import attendance as svc_attendance
        from app.services import mahasiswa as svc_mahasiswa

        foto_a = _cari_foto(0)
        foto_b = _cari_foto(1)
        if foto_a is None or foto_b is None:
            self.skipTest("Foto dataset tidak cukup")
        a = imread_bytes(_kirim_bytes(foto_a))
        b = imread_bytes(_kirim_bytes(foto_b))
        komposit = np.hstack([cv2.resize(a, (360, 360)), cv2.resize(b, (360, 360))])
        hasil = engine.recognize_all(komposit)

        session = common.make_sesi(common.make_kelas())
        session_row = svc_attendance.get_open_session(session)

        tercatat = 0
        urut = 0
        for face in hasil["faces"]:
            if not face["recognized"]:
                continue
            urut += 1
            mhs = svc_mahasiswa.find_by_person_label(face["person"])
            if mhs is None:
                # Daftarkan mahasiswa dengan person_label yang dikenali galeri.
                mhs_id = common.make_mahasiswa(
                    f"0901118232{urut:03d}",
                    face["nama"] or face["person"],
                    face["person"],
                )
                mhs = {"id": mhs_id}
            common.add_member(session_row["class_id"], int(mhs["id"]))
            outcome = svc_attendance.record_attendance(
                session=session_row,
                mahasiswa_id=int(mhs["id"]),
                similarity=face["similarity"],
                det_status=face["det_status"],
                n_wajah=hasil["n_faces"],
                bbox=face["box"],
            )
            if outcome.status == "tercatat":
                tercatat += 1

        from app.db.engine import db

        total = db.query_value("SELECT COUNT(*) FROM kehadiran", (), 0)
        self.assertEqual(total, tercatat)
        self.assertGreaterEqual(total, 1)
        # Tidak ada duplikasi untuk mahasiswa yang sama.
        duplikat = db.query_one(
            """SELECT session_id, mahasiswa_id, COUNT(*) AS n FROM kehadiran
               GROUP BY session_id, mahasiswa_id HAVING n > 1"""
        )
        self.assertIsNone(duplikat)


if __name__ == "__main__":
    unittest.main()
