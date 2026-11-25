"""Skema request/response (pydantic) untuk API V2."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class MahasiswaIn(BaseModel):
    nim: str = Field(min_length=6, max_length=24)
    nama: str = Field(min_length=2, max_length=120)
    email: Optional[str] = Field(default=None, max_length=160)
    no_hp: Optional[str] = Field(default=None, max_length=32)
    aktif: bool = True

    @field_validator("nim")
    @classmethod
    def _nim(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) < 6:
            raise ValueError("NIM harus berupa angka minimal 6 digit.")
        return v

    @field_validator("nama")
    @classmethod
    def _nama(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Nama minimal 2 karakter.")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Format email tidak valid.")
        return v


class MahasiswaUpdate(BaseModel):
    nim: Optional[str] = None
    nama: Optional[str] = None
    email: Optional[str] = None
    no_hp: Optional[str] = None
    aktif: Optional[bool] = None


class DosenIn(BaseModel):
    nama: str = Field(min_length=2, max_length=120)
    email: Optional[str] = None
    nid: Optional[str] = None
    no_hp: Optional[str] = None
    buat_akun: bool = True
    username: Optional[str] = None
    password: Optional[str] = None


class DosenUpdate(BaseModel):
    nama: Optional[str] = None
    email: Optional[str] = None
    nid: Optional[str] = None
    no_hp: Optional[str] = None
    aktif: Optional[bool] = None


class KelasIn(BaseModel):
    kode_mk: str = Field(min_length=1, max_length=40)
    nama_mk: str = Field(min_length=2, max_length=160)
    label: str = Field(min_length=1, max_length=40)
    dosen_id: Optional[int] = None
    tahun_akademik: Optional[str] = None
    semester: Optional[str] = None
    status: str = "aktif"

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        return v if v in ("aktif", "nonaktif") else "aktif"


class KelasUpdate(BaseModel):
    kode_mk: Optional[str] = None
    nama_mk: Optional[str] = None
    label: Optional[str] = None
    dosen_id: Optional[int] = None
    tahun_akademik: Optional[str] = None
    semester: Optional[str] = None
    status: Optional[str] = None


class SesiIn(BaseModel):
    class_id: int
    tanggal: str
    mulai_jam: str
    selesai_jam: Optional[str] = None
    keterangan: Optional[str] = None

    @field_validator("tanggal")
    @classmethod
    def _tanggal(cls, v: str) -> str:
        from datetime import datetime

        try:
            datetime.strptime(v.strip(), "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Tanggal harus format YYYY-MM-DD.") from exc
        return v.strip()

    @field_validator("mulai_jam", "selesai_jam")
    @classmethod
    def _jam(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        from datetime import datetime

        try:
            datetime.strptime(v.strip(), "%H:%M")
        except ValueError as exc:
            raise ValueError("Jam harus format HH:MM.") from exc
        return v.strip()


class KoreksiHadirIn(BaseModel):
    status: str = Field(description="HADIR | TOLAK | MANUAL")
    catatan: Optional[str] = Field(default=None, max_length=500)

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in ("HADIR", "TOLAK", "MANUAL"):
            raise ValueError("Status harus HADIR, TOLAK, atau MANUAL.")
        return v


class KoreksiManualIn(BaseModel):
    session_id: int
    mahasiswa_id: int
    status: str = "MANUAL"
    catatan: Optional[str] = Field(default=None, max_length=500)


class PasswordIn(BaseModel):
    password: str = Field(min_length=10, max_length=200)


class EnrollmentWajahIn(BaseModel):
    """Enrollment wajah baru oleh admin.

    Gambar dikirim sebagai file (multipart) pada endpoint khusus; skema ini
    hanya membawa metadata.
    """

    catatan: Optional[str] = Field(default=None, max_length=300)
    aktif: bool = True


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class KoreksiResponse(BaseModel):
    ok: bool
    detail: Any = None
