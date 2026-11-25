"""Mesin pengenalan wajah V2 â€” multi-face.

Mesin ini membaca artefak model secara READ-ONLY:
  * bobot   : ``kapitaselekta\\model\\inception_resnet_v1_vggface2.pt``
  * galeri  : ``Kapita\\gallery.npz`` + ``gallery_meta.json``
  * ambang  : ``Kapita\\threshold.txt``

Fitur baru dibanding V1:
  * **Multi-face detection** â€” MTCNN dengan ``keep_all=True`` mengembalikan
    seluruh wajah pada frame (dibuktikan pada audit V1).
  * **Multi-face recognition** â€” setiap wajah di-align, di-embed (satu forward
    batch), lalu dicocokkan ke galeri secara independen.
  * **Multi-face attendance** â€” ditangani di service attendance: setiap identitas
    dicatat terpisah, dengan unique constraint di database.

Keamanan data biometrik: embedding tidak pernah dikirim ke browser.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np

from app import config
from app.face import pipeline
from app.face.gallery import Gallery, gallery_loader


@dataclass
class FaceResult:
    """Hasil pengenalan untuk SATU wajah pada satu frame."""

    index: int
    box: list[float]
    similarity: Optional[float] = None
    person: str = ""
    nama: str = pipeline.LABEL_UNKNOWN
    nim: str = ""
    kelas: str = ""
    recognized: bool = False
    reason: str = ""  # match | below_threshold | no_face | error
    det_status: str = ""
    enrolled: bool = True  # True bila identitas berasal dari galeri/enrollment aktif

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "box": [round(float(v), 1) for v in self.box],
            "similarity": None if self.similarity is None else round(float(self.similarity), 4),
            "person": self.person,
            "nama": self.nama,
            "nim": self.nim,
            "kelas": self.kelas,
            "recognized": self.recognized,
            "reason": self.reason,
            "det_status": self.det_status,
            "enrolled": self.enrolled,
        }


@dataclass
class EngineStatus:
    loaded: bool = False
    error: Optional[str] = None
    device: str = "cpu"
    threshold: float = config.DEFAULT_THRESHOLD
    n_persons: int = 0
    model_source: str = ""
    gallery_source: str = ""
    loaded_at: Optional[float] = None
    last_latency_ms: Optional[float] = None
    threshold_meta: dict = field(default_factory=dict)
    enrolled_from_db: int = 0


class FaceEngine:
    """Wrapper inferensi multi-wajah; lazy-load & thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._last_infer = 0.0
        self.status = EngineStatus()
        self._net = None
        self._mtcnn = None
        self._mtcnn_longgar = None
        self._gallery: Optional[Gallery] = None

    # -- pemuatan ---------------------------------------------------------
    def ensure_loaded(self) -> bool:
        if self.status.loaded:
            return True
        with self._lock:
            if self.status.loaded:
                return True
            try:
                self._load()
                self.status.loaded = True
                self.status.error = None
            except Exception as exc:  # pragma: no cover - bergantung artefak
                self.status.error = f"{type(exc).__name__}: {exc}"
                return False
        return True

    def _load(self) -> None:
        import torch
        from facenet_pytorch import InceptionResnetV1, MTCNN

        if config.FACE_DEVICE:
            device = torch.device(config.FACE_DEVICE)
        else:
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.status.device = str(device)

        gallery = gallery_loader.load()
        # Gabungkan enrollment `upload` dari DB V2 agar tetap dikenali setelah restart.
        enrolled = gallery_loader.merge_db_enrollments(gallery)
        if enrolled:
            self.status.enrolled_from_db = enrolled
        self._gallery = gallery
        self.status.threshold = gallery.threshold
        self.status.threshold_meta = gallery.threshold_meta
        self.status.n_persons = gallery.count
        self.status.gallery_source = gallery.source

        if config.MODEL_PT.exists():
            net = InceptionResnetV1(
                pretrained=None, classify=True, num_classes=config.MODEL_NUM_CLASSES
            )
            state = torch.load(str(config.MODEL_PT), map_location="cpu", weights_only=True)
            net.load_state_dict(state, strict=True)
            net.classify = False
            self.status.model_source = str(config.MODEL_PT)
        else:
            net = InceptionResnetV1(pretrained="vggface2")
            self.status.model_source = "torch-cache:vggface2"
        net.eval().to(device)
        for prm in net.parameters():
            prm.requires_grad_(False)
        self._net = net

        # keep_all=True -> MTCNN mengembalikan SELURUH wajah pada frame.
        self._mtcnn = MTCNN(
            keep_all=True, select_largest=True, min_face_size=config.MIN_FACE_SIZE, device=device
        )
        self._mtcnn_longgar = MTCNN(
            keep_all=True,
            select_largest=True,
            min_face_size=10,
            thresholds=[0.5, 0.6, 0.5],
            device=device,
        )
        self.status.loaded_at = time.time()

    # -- deteksi ----------------------------------------------------------
    def _detect(self, img: np.ndarray):
        """Rantai kanonik: MTCNN -> CLAHE -> gamma. Mengembalikan (boxes, points, status)."""
        percobaan = [
            (pipeline.STATUS_MTCNN, img, self._mtcnn),
            (pipeline.STATUS_CLAHE, pipeline.clahe_bright(img), self._mtcnn),
            (pipeline.STATUS_GAMMA, pipeline.gamma_bright(img, 0.5), self._mtcnn_longgar),
        ]
        for status, gambar, model in percobaan:
            rgb = cv2.cvtColor(gambar, cv2.COLOR_BGR2RGB)
            boxes, probs, points = model.detect(rgb, landmarks=True)
            if boxes is None or len(boxes) == 0 or points is None:
                continue
            boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
            points = np.asarray(points, dtype=np.float32).reshape(-1, 5, 2)
            if len(boxes) == 0:
                continue
            return boxes, points, status
        return np.zeros((0, 4), np.float32), np.zeros((0, 5, 2), np.float32), pipeline.STATUS_NOFACE

    # -- embedding --------------------------------------------------------
    def _embed(self, faces: list[np.ndarray]) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        x = torch.stack(
            [torch.from_numpy(pipeline.preprocess_for_embedding(f)) for f in faces]
        ).to(self.status.device)
        with torch.no_grad():
            y = self._net(x)
            y = F.normalize(y, p=2, dim=1)
        return y.detach().cpu().numpy().astype(np.float32)

    # -- pencocokan -------------------------------------------------------
    def _match(self, embeddings: np.ndarray) -> list[tuple[str, float]]:
        """Cocokkan embedding ke galeri. Mengembalikan list (person, similarity)."""
        gallery = self._gallery
        if gallery is None or gallery.matrix is None or gallery.matrix.size == 0:
            return [("", 0.0) for _ in range(len(embeddings))]
        sims = gallery.matrix @ embeddings.T  # (N_gallery, N_faces)
        out: list[tuple[str, float]] = []
        for i in range(embeddings.shape[0]):
            j = int(np.argmax(sims[:, i]))
            out.append((gallery.entries[j].person, float(sims[j, i])))
        return out

    # -- API utama --------------------------------------------------------
    def recognize_all(self, bgr: np.ndarray, max_faces: Optional[int] = None) -> dict:
        """Kenali SEMUA wajah pada satu frame.

        Mengembalikan dict:
            {"ok": bool, "det_status": str, "n_faces": int, "faces": [FaceResult...]}
        """
        if bgr is None or getattr(bgr, "size", 0) == 0:
            raise ValueError("gambar tidak dapat dibaca")
        if not self.ensure_loaded():
            raise RuntimeError(f"engine tidak siap: {self.status.error}")

        limit = max_faces or config.MAX_FACES_PER_FRAME
        img = pipeline.downscale_max_side(bgr)

        with self._infer_lock:
            self._throttle()
            started = time.perf_counter()
            boxes, points, det_status = self._detect(img)
            n_detected = len(boxes)

            faces: list[np.ndarray] = []
            kept_boxes: list[list[float]] = []
            order = pipeline.sort_boxes_by_area(boxes)
            for idx in order[:limit]:
                face = pipeline.align_face(img, boxes[idx], points[idx])
                if face is None:
                    continue
                faces.append(face)
                kept_boxes.append([float(v) for v in boxes[idx]])

            results: list[FaceResult] = []
            if not faces:
                self.status.last_latency_ms = (time.perf_counter() - started) * 1000.0
                return {
                    "ok": True,
                    "det_status": det_status,
                    "n_faces": 0,
                    "n_detected": 0,
                    "faces": [],
                    "latency_ms": round(self.status.last_latency_ms, 1),
                }

            embeddings = self._embed(faces)
            matches = self._match(embeddings)
            threshold = self.status.threshold
            gallery = self._gallery
            entry_by_person = {e.person: e for e in (gallery.entries if gallery else [])}

            for i, (person, score) in enumerate(matches):
                entry = entry_by_person.get(person)
                kena = bool(entry) and score >= threshold
                results.append(
                    FaceResult(
                        index=i,
                        box=kept_boxes[i],
                        similarity=score,
                        person=person if kena else "",
                        nama=(entry.nama if entry else pipeline.LABEL_UNKNOWN) if kena
                        else pipeline.LABEL_UNKNOWN,
                        nim=(entry.nim if entry else "") if kena else "",
                        kelas=(entry.kelas if entry else "") if kena else "",
                        recognized=kena,
                        reason="match" if kena else "below_threshold",
                        det_status=det_status,
                    )
                )

            self.status.last_latency_ms = (time.perf_counter() - started) * 1000.0
            return {
                "ok": True,
                "det_status": det_status,
                "n_faces": len(results),
                "n_detected": n_detected,
                "faces": [r.to_dict() for r in results],
                "latency_ms": round(self.status.last_latency_ms, 1),
                "threshold": threshold,
            }

    def _throttle(self) -> None:
        """Batasi frekuensi inferensi (server-side) bila dikonfigurasi."""
        interval = config.INFER_MIN_INTERVAL_MS / 1000.0
        if interval <= 0:
            return
        elapsed = time.perf_counter() - self._last_infer
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_infer = time.perf_counter()

    # -- enrollment --------------------------------------------------------
    def embed_single(self, bgr: np.ndarray) -> Optional[np.ndarray]:
        """Hitung embedding satu wajah (dipakai enrollment admin)."""
        if not self.ensure_loaded():
            raise RuntimeError(f"engine tidak siap: {self.status.error}")
        img = pipeline.downscale_max_side(bgr)
        with self._infer_lock:
            boxes, points, det_status = self._detect(img)
            if len(boxes) == 0:
                return None
            idx = int(pipeline.sort_boxes_by_area(boxes)[0])
            face = pipeline.align_face(img, boxes[idx], points[idx])
            if face is None:
                return None
            return self._embed([face])[0]

    def register_embedding(self, person: str, nama: str, nim: str, kelas: str,
                           embedding: np.ndarray) -> None:
        """Tambahkan identitas enrollment baru ke galeri aktif (in-memory V2)."""
        from app.face.gallery import GalleryEntry

        gallery_loader.add_entry(
            GalleryEntry(person=person, nama=nama, nim=nim, kelas=kelas,
                         embedding=np.asarray(embedding, dtype=np.float32))
        )
        self._gallery = gallery_loader.load()
        gallery_loader.merge_db_enrollments(self._gallery)
        self.status.n_persons = self._gallery.count


engine = FaceEngine()

