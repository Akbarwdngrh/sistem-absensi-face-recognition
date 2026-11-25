"""Utilitas keamanan V2: hashing kata sandi, token sesi, CSRF, rate limit.

Tidak menambah dependensi baru: hashing memakai argon2-cffi, token & CSRF
memakai `secrets` + `hmac` dari pustaka standar.

Perbedaan dari V1 ( disengaja, didokumentasikan):
  * Role `mahasiswa` dihapus dari tabel akun — mahasiswa V2 tidak punya akun.
  * Panjang minimum kata sandi dinaikkan menjadi 10 (dapat dikonfigurasi).
  * Gagal login dicatat sebagai audit dengan IP, dan diberi rate limit.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Deque, Dict, Optional, Tuple

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Kata sandi
# ---------------------------------------------------------------------------
def validate_password(
    password: Optional[str],
    min_length: int = 10,
    *,
    username: str = "",
    nim: str = "",
    nama: str = "",
) -> Optional[str]:
    """Periksa kebijakan kata sandi. Mengembalikan pesan error atau None."""
    if not password:
        return "Kata sandi wajib diisi."
    if len(password) < min_length:
        return f"Kata sandi minimal {min_length} karakter."
    if not password.strip():
        return "Kata sandi tidak boleh hanya berisi spasi."
    if len(set(password)) == 1:
        return "Kata sandi tidak boleh berupa satu karakter yang sama."
    if password.strip() != password:
        return "Kata sandi tidak boleh diawali/diakhiri spasi."
    lowered = password.lower()
    for value in (username, nim, nama):
        value = (value or "").strip()
        if value and len(value) >= 4 and lowered == value.lower():
            return "Kata sandi tidak boleh sama dengan username, NIM, atau nama."
    if password.lower() in ("password", "password123", "admin123", "12345678", "qwerty123"):
        return "Kata sandi terlalu umum. Pilih yang lebih unik."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Token sesi
# ---------------------------------------------------------------------------
def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_expiry(hours: int) -> str:
    return (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def csrf_for(secret: str, token: str) -> str:
    """Token CSRF terautentikasi: HMAC-SHA256(secret, token_sesi)."""
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_matches(secret: str, session_token: str, provided: str) -> bool:
    if not session_token or not provided:
        return False
    expected = csrf_for(secret, session_token)
    return hmac.compare_digest(expected, provided)


def new_public_csrf() -> str:
    """Token acak untuk double-submit cookie pada halaman publik."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Rate limit (in-memory, per proses)
# ---------------------------------------------------------------------------
class RateLimiter:
    """Sliding window per kunci. Cukup untuk satu proses uvicorn."""

    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """True bila request masih di bawah batas."""
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def remaining(self, key: str, limit: int, window_seconds: int) -> int:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return max(0, limit - len(bucket))

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()


def client_ip(request) -> str:
    """Ambil IP klien (hanya untuk audit & rate limit, bukan untuk otorisasi)."""
    try:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    except Exception:
        pass
    try:
        return (request.client.host if request.client else "unknown")[:64]
    except Exception:
        return "unknown"


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


def random_password(length: int = 14) -> str:
    """Sandi acak yang mudah dibaca (tanpa karakter mirip O/0, l/1/I)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def split_kv(detail: Optional[str]) -> Tuple[str, str]:
    """Pecah string 'k=v' menjadi (k, v) untuk detail audit."""
    if not detail:
        return "", ""
    if "=" not in detail:
        return detail, ""
    key, _, value = detail.partition("=")
    return key, value
