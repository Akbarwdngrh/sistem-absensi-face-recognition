"""Konfigurasi aplikasi V2.

PENTING: konfigurasi ini sepenuhnya terpisah dari V1. Tidak ada satu pun path,
secret, atau variabel yang dibaca dari folder V1 atau `.env` V1.

Semua nilai dapat dioverride melalui environment variable atau file `.env` milik
V2 sendiri (`Dev2/.env`).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _path(name: str, default: str) -> Path:
    """Path dari env/.env; path relatif diselesaikan terhadap folder V2."""
    value = Path(_env(name, default))
    return value if value.is_absolute() else (BASE_DIR / value)


# ---------------------------------------------------------------------------
# Direktori kerja (semua output V2 berada DI DALAM Dev2)
# ---------------------------------------------------------------------------
DATA_DIR = _path("DATA_DIR", "data")
EXPORT_DIR = _path("EXPORT_DIR", "exports")
LOG_DIR = _path("LOG_DIR", "logs")
DATABASE_PATH = _path("DATABASE_PATH", str(DATA_DIR / "absensi_v2.db"))
UPLOAD_DIR = _path("UPLOAD_DIR", str(DATA_DIR / "face_uploads"))

# DSN opsional untuk database server (PostgreSQL). Bila diisi, V2 memakai
# PostgreSQL; skema SQL ditulis portabel (lihat app/db/engine.py).
DATABASE_URL = _env("DATABASE_URL", "")

# ---------------------------------------------------------------------------
# Artefak model wajah — READ-ONLY (opsional bila memakai enrollment DB V2)
# ---------------------------------------------------------------------------
# Folder galeri, metadata, dan ambang (default relatif terhadap Dev2/).
MODEL_DATA_ROOT = _path("MODEL_DATA_ROOT", "artifacts")
# Folder bobot model lokal (bila tidak ada, engine otomatis mengunduh VGGFace2).
MODEL_WEIGHTS_ROOT = _path("MODEL_WEIGHTS_ROOT", "model")

GALLERY_NPZ = _path("GALLERY_NPZ", str(MODEL_DATA_ROOT / "gallery.npz"))
GALLERY_META = _path("GALLERY_META", str(MODEL_DATA_ROOT / "gallery_meta.json"))
THRESHOLD_FILE = _path("THRESHOLD_FILE", str(MODEL_DATA_ROOT / "threshold.txt"))
MODEL_PT = _path("MODEL_PT", str(MODEL_WEIGHTS_ROOT / "inception_resnet_v1_vggface2.pt"))

# Jumlah kelas kepala klasifikasi VGGFace2 agar state_dict lokal dapat dimuat utuh.
MODEL_NUM_CLASSES = _env_int("MODEL_NUM_CLASSES", 8631)
# Ambang cadangan bila threshold.txt tidak terbaca. Nilai ini WAJIB sama dengan
# artefak riset (0.695) dan hanya boleh diubah lewat uji akurasi/FAR.
DEFAULT_THRESHOLD = _env_float("DEFAULT_THRESHOLD", 0.695)

# Device inferensi: "" = otomatis (cuda bila tersedia), "cpu" = paksa CPU.
FACE_DEVICE = _env("FACE_DEVICE", "")
LOAD_ENGINE_ON_STARTUP = _env_bool("LOAD_ENGINE_ON_STARTUP", True)

# ---------------------------------------------------------------------------
# Parameter pipeline (KANONIK — jangan diubah tanpa pengujian akurasi)
# ---------------------------------------------------------------------------
IMG_SIZE = 160
CROP_FACTOR = 1.15
ROLL_CORRECT = True
MAX_SIDE = 1600
MIN_FACE_SIZE = 20
# Jumlah wajah maksimum yang diproses per frame (pembatas beban CPU/GPU).
MAX_FACES_PER_FRAME = _env_int("MAX_FACES_PER_FRAME", 12)
# Jumlah wajah minimum agar frame dianggap layak (bukan memaksa wajah ke kotak).
MIN_FACES_FOR_ABSEN = 1

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
APP_NAME = "Sistem Absensi Face Recognition V2"
APP_VERSION = "2.0.0"
HOST = _env("HOST", "127.0.0.1")
PORT = _env_int("PORT", 8010)
DEBUG = _env_bool("DEBUG", False)

# ---------------------------------------------------------------------------
# Keamanan / sesi
# ---------------------------------------------------------------------------
SECRET_KEY = _env("SECRET_KEY", "")
SESSION_HOURS = _env_int("SESSION_HOURS", 12)
COOKIE_SECURE = _env_bool("COOKIE_SECURE", False)
COOKIE_NAME = "absensi_v2_session"
CSRF_COOKIE_NAME = "absensi_v2_csrf"
PASSWORD_MIN_LENGTH = _env_int("PASSWORD_MIN_LENGTH", 10)

# Halaman absensi publik (mahasiswa tanpa login).
PUBLIC_ATTENDANCE_ENABLED = _env_bool("PUBLIC_ATTENDANCE_ENABLED", True)
# Admin boleh mendaftarkan wajah baru (embedding dihitung V2, disimpan di DB V2).
ALLOW_FACE_ENROLLMENT = _env_bool("ALLOW_FACE_ENROLLMENT", True)

# ---------------------------------------------------------------------------
# Rate limiting (sederhana, in-memory, per proses)
# ---------------------------------------------------------------------------
RATE_LIMIT_LOGIN_MAX = _env_int("RATE_LIMIT_LOGIN_MAX", 8)
RATE_LIMIT_LOGIN_WINDOW = _env_int("RATE_LIMIT_LOGIN_WINDOW", 300)
RATE_LIMIT_PUBLIC_MAX = _env_int("RATE_LIMIT_PUBLIC_MAX", 60)
RATE_LIMIT_PUBLIC_WINDOW = _env_int("RATE_LIMIT_PUBLIC_WINDOW", 60)
RATE_LIMIT_API_MAX = _env_int("RATE_LIMIT_API_MAX", 120)
RATE_LIMIT_API_WINDOW = _env_int("RATE_LIMIT_API_WINDOW", 60)

# ---------------------------------------------------------------------------
# Batasan request
# ---------------------------------------------------------------------------
MAX_FRAME_BYTES = _env_int("MAX_FRAME_BYTES", 4 * 1024 * 1024)
MAX_IMAGE_DIM = _env_int("MAX_IMAGE_DIM", 1600)
# Jeda minimum antar inferensi pada proses yang sama (server-side throttle).
INFER_MIN_INTERVAL_MS = _env_int("INFER_MIN_INTERVAL_MS", 0)
# Masa pendinginan per identitas (detik) sebelum identitas yang sama diproses ulang.
IDENTITY_COOLDOWN_SECONDS = _env_int("IDENTITY_COOLDOWN_SECONDS", 3)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
LOG_FILE = _path("LOG_FILE", str(LOG_DIR / "absensi_v2.log"))


def ensure_dirs() -> None:
    """Buat seluruh direktori kerja V2 bila belum ada."""
    for path in (DATA_DIR, EXPORT_DIR, LOG_DIR, UPLOAD_DIR, DATABASE_PATH.parent):
        path.mkdir(parents=True, exist_ok=True)
