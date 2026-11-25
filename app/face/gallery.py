"""Galeri biometrik (READ-ONLY) + ambang keputusan.

Artefak yang dibaca:
  * ``gallery.npz``      â€” centroid embedding (69 x 512) + label person
  * ``gallery_meta.json`` â€” metadata identitas (nama, NIM, kelas)
  * ``threshold.txt``    â€” ambang cosine + metadata evaluasi

Modul ini HANYA membaca. Tidak pernah menulis ke folder riset/model.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from app import config

_log = logging.getLogger("absensi_v2.gallery")


@dataclass
class GalleryEntry:
    """Satu identitas dalam galeri."""

    person: str
    nama: str = ""
    nim: str = ""
    kelas: str = ""
    embedding: Optional[np.ndarray] = None  # 512-d, L2-normalized


@dataclass
class Gallery:
    """Kumpulan embedding rujukan beserta metadata."""

    entries: list[GalleryEntry] = field(default_factory=list)
    matrix: Optional[np.ndarray] = None  # (N, 512) float32
    threshold: float = config.DEFAULT_THRESHOLD
    threshold_meta: dict = field(default_factory=dict)
    source: str = ""
    active_count: int = 0

    @property
    def count(self) -> int:
        return len(self.entries)

    def index_by_person(self) -> dict[str, int]:
        return {e.person: i for i, e in enumerate(self.entries)}


def read_threshold(path: Optional[Path] = None) -> tuple[float, dict]:
    """Baca ambang cosine dari file riset (baris pertama = nilai)."""
    path = Path(path or config.THRESHOLD_FILE)
    value: Optional[float] = None
    meta: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    meta[key.strip()] = val.strip()
                else:
                    try:
                        value = float(line)
                    except ValueError:
                        continue
    except OSError:
        pass
    if value is None:
        value = config.DEFAULT_THRESHOLD
    return value, meta


class GalleryLoader:
    """Memuat galeri sekali lalu menyimpannya di memori (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gallery: Optional[Gallery] = None
        self._key: Optional[tuple] = None

    def load(self, force: bool = False) -> Gallery:
        with self._lock:
            key = self._signature()
            if not force and self._gallery is not None and key == self._key:
                return self._gallery
            gallery = self._load_now()
            self._gallery = gallery
            self._key = key
            return gallery

    def _signature(self) -> tuple:
        def mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return -1.0

        return (
            str(config.GALLERY_NPZ), mtime(config.GALLERY_NPZ),
            str(config.GALLERY_META), mtime(config.GALLERY_META),
            str(config.THRESHOLD_FILE), mtime(config.THRESHOLD_FILE),
        )

    def _load_now(self) -> Gallery:
        threshold, threshold_meta = read_threshold()
        npz_path = Path(config.GALLERY_NPZ)
        if not npz_path.exists():
            _log.info(
                "File gallery.npz tidak ditemukan (%s) — berjalan dalam mode galeri DB saja.",
                npz_path,
            )
            return Gallery(
                entries=[],
                matrix=np.zeros((0, 512), np.float32),
                threshold=threshold,
                threshold_meta=threshold_meta,
                source="empty (db-enrollment-only)",
                active_count=0,
            )

        npz = np.load(str(npz_path), allow_pickle=False)
        embeddings = npz["embeddings"].astype(np.float32)
        persons = [str(p) for p in npz["person"]]
        aktif = npz["aktif"].astype(bool) if "aktif" in npz.files else np.ones(len(persons), bool)

        meta_by_person: dict[str, dict] = {}
        try:
            with open(config.GALLERY_META, encoding="utf-8") as fh:
                raw = json.load(fh)
            for item in raw.get("orang", []):
                person = str(item.get("person") or "")
                if person:
                    meta_by_person[person] = item
        except (OSError, ValueError):
            meta_by_person = {}

        entries: list[GalleryEntry] = []
        vectors: list[np.ndarray] = []
        for idx, person in enumerate(persons):
            if not aktif[idx]:
                continue
            item = meta_by_person.get(person, {})
            vector = embeddings[idx]
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vector = vector / norm
            vectors.append(vector.astype(np.float32))
            entries.append(
                GalleryEntry(
                    person=person,
                    nama=str(item.get("nama") or person),
                    nim=str(item.get("nim") or ""),
                    kelas=str(item.get("kelas") or ""),
                    embedding=vector.astype(np.float32),
                )
            )

        matrix = np.vstack(vectors).astype(np.float32) if vectors else np.zeros((0, 512), np.float32)
        return Gallery(
            entries=entries,
            matrix=matrix,
            threshold=threshold,
            threshold_meta=threshold_meta,
            source=str(npz_path),
            active_count=int(aktif.sum()),
        )

    def add_entry(self, entry: GalleryEntry) -> Gallery:
        """Tambahkan embedding enrollment baru ke galeri aktif (hanya di memori V2).

        Artefak riset tidak pernah disentuh; embedding baru disimpan di DB V2
        dan hanya hidup selama proses berjalan.
        """
        gallery = self.load()
        if entry.person in gallery.index_by_person():
            return gallery
        vector = entry.embedding
        if vector is None:
            return gallery
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector = (vector / norm).astype(np.float32)
        gallery.entries.append(entry)
        gallery.matrix = np.vstack([gallery.matrix, vector[None, :]]).astype(np.float32)
        return gallery

    def merge_db_enrollments(self, gallery: Gallery) -> int:
        """Gabungkan enrollment `upload` dari DB V2 ke galeri aktif.

        Dipanggil saat engine start agar identitas yang di-enroll admin tetap
        dikenali setelah aplikasi dimulai ulang. Artefak riset tidak ditulis.
        """
        try:
            from app.db.engine import db
        except Exception:
            return 0

        try:
            rows = db.query_all(
                """SELECT fe.person_label, fe.embedding, fe.dimensi,
                          m.nama, m.nim
                   FROM face_enrollments fe
                   JOIN mahasiswa m ON m.id = fe.mahasiswa_id
                   WHERE fe.aktif = 1 AND fe.embedding IS NOT NULL
                     AND fe.sumber <> 'gallery'"""
            )
        except Exception:
            return 0

        known = gallery.index_by_person()
        jumlah = 0
        for row in rows:
            person = str(row.get("person_label") or "")
            blob = row.get("embedding")
            if not person or not blob or person in known:
                continue
            try:
                vektor = np.frombuffer(blob, dtype=np.float32)
                dim = int(row.get("dimensi") or vektor.size)
                if dim and vektor.size != dim:
                    continue
                gallery.entries.append(
                    GalleryEntry(
                        person=person,
                        nama=str(row.get("nama") or person),
                        nim=str(row.get("nim") or ""),
                        kelas="",
                        embedding=vektor,
                    )
                )
                norm = float(np.linalg.norm(vektor))
                if norm > 0:
                    vektor = vektor / norm
                gallery.matrix = np.vstack([gallery.matrix, vektor[None, :]]).astype(np.float32)
                jumlah += 1
            except Exception:
                continue
        return jumlah


gallery_loader = GalleryLoader()

