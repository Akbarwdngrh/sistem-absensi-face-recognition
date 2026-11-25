"""Pipeline wajah KANONIK V2.

Seluruh fungsi di sini adalah **copy logika** dari `Dev/inference.py` (V1) dan
`Kapita/live_absensi_v2.py` (riset), agar representasi embedding V2 tetap
kompatibel dengan galeri yang sudah ada.

Perbedaan satu-satunya terhadap V1: V1 mengambil **wajah terbesar** (`pick_largest_face`),
sedangkan V2 memproses **semua wajah** pada frame secara individual.

Aturan yang tidak boleh diubah tanpa uji akurasi/FAR:
  IMG_SIZE=160, CROP_FACTOR=1.15, koreksi roll > 0.5°, normalisasi (px-127.5)/128,
  MTCNN(min_face_size=20) dengan fallback CLAHE lalu gamma(0.5)+MTCNN longgar.
"""

from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np

from app import config

# ---------------------------------------------------------------------------
# Konstanta kanonik
# ---------------------------------------------------------------------------
IMG_SIZE = config.IMG_SIZE
CROP_FACTOR = config.CROP_FACTOR
ROLL_CORRECT = config.ROLL_CORRECT
MAX_SIDE = config.MAX_SIDE

LABEL_UNKNOWN = "Tidak dikenal"

STATUS_MTCNN = "mtcnn"
STATUS_CLAHE = "mtcnn_clahe"
STATUS_GAMMA = "mtcnn_gamma"
STATUS_NOFACE = "tidak_ada_wajah"

_STATUS_TEXT = {
    STATUS_MTCNN: "wajah terdeteksi",
    STATUS_CLAHE: "terdeteksi setelah pencerahan CLAHE",
    STATUS_GAMMA: "terdeteksi setelah koreksi gamma",
    STATUS_NOFACE: "wajah tidak terdeteksi",
}


def status_text(status: str) -> str:
    return _STATUS_TEXT.get(status, status or "")


# ---------------------------------------------------------------------------
# IO gambar
# ---------------------------------------------------------------------------
def imread_bytes(data: bytes) -> Optional[np.ndarray]:
    """Decode bytes gambar (upload browser) menjadi BGR uint8.

    Fallback Pillow (+pillow-heif) bila OpenCV gagal (mis. HEIC dari ponsel).
    """
    if not data:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is not None:
        return img
    try:
        import io

        from PIL import Image

        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
            return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def downscale_max_side(img: np.ndarray, max_side: int = MAX_SIDE) -> np.ndarray:
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        s = max_side / float(max(h, w))
        img = cv2.resize(
            img, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA
        )
    return img


# ---------------------------------------------------------------------------
# Pencerahan (fallback deteksi)
# ---------------------------------------------------------------------------
def clahe_bright(bgr: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def gamma_bright(bgr: np.ndarray, g: float = 0.5) -> np.ndarray:
    lut = np.array([((i / 255.0) ** g) * 255.0 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(bgr, lut)


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------
def roll_degrees(landmarks5: Any) -> float:
    pts = np.asarray(landmarks5, dtype=np.float32).reshape(-1, 2)
    d = pts[1] - pts[0]
    return float(np.degrees(np.arctan2(float(d[1]), float(d[0]))))


def center_square_fallback(bgr: np.ndarray, canvas: int = IMG_SIZE) -> np.ndarray:
    h, w = bgr.shape[:2]
    s = min(h, w)
    y0 = (h - s) // 2
    x0 = (w - s) // 2
    return cv2.resize(
        bgr[y0 : y0 + s, x0 : x0 + s], (canvas, canvas), interpolation=cv2.INTER_AREA
    )


def align_face(
    bgr: np.ndarray,
    box: Any,
    landmarks5: Any,
    canvas: int = IMG_SIZE,
    crop_factor: float = CROP_FACTOR,
    roll_correct: bool = ROLL_CORRECT,
) -> Optional[np.ndarray]:
    """Wajah ter-align 160x160 dari bbox DETEKTOR + 5 landmark (kanonik V1)."""
    x0, y0, x1, y1 = [float(v) for v in box]
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    side = max(x1 - x0, y1 - y0) * float(crop_factor)

    img = bgr
    if roll_correct and landmarks5 is not None:
        sudut = roll_degrees(landmarks5)
        if abs(sudut) > 0.5:
            M = cv2.getRotationMatrix2D((cx, cy), sudut, 1.0)
            img = cv2.warpAffine(
                img,
                M,
                (img.shape[1], img.shape[0]),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )

    h, w = img.shape[:2]
    sx1 = int(max(0, round(cx - side / 2)))
    sy1 = int(max(0, round(cy - side / 2)))
    sx2 = int(min(w, round(cx + side / 2)))
    sy2 = int(min(h, round(cy + side / 2)))
    if sx2 - sx1 < 8 or sy2 - sy1 < 8:
        return center_square_fallback(bgr, canvas)
    crop = img[sy1:sy2, sx1:sx2]
    return cv2.resize(crop, (canvas, canvas), interpolation=cv2.INTER_AREA)


def sort_boxes_by_area(boxes: np.ndarray) -> np.ndarray:
    """Urutkan index box dari luas terbesar ke terkecil."""
    boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return np.argsort(-area)


def preprocess_for_embedding(face_bgr: np.ndarray) -> np.ndarray:
    """(H, W, 3) BGR uint8 -> (3, H, W) float32 ternormalisasi (kanonik V1)."""
    rgb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    return ((rgb - 127.5) / 128.0).transpose(2, 0, 1)

