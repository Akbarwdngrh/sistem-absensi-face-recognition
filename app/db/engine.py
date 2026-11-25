"""Lapisan akses data V2.

Diensain agar perpindahan SQLite -> PostgreSQL hanya mengubah konfigurasi
(`DATABASE_URL`), bukan kueri di seluruh aplikasi:

  * Placeholder ditulis sebagai `?` dan diterjemahkan otomatis ke `%s` bila
    backend adalah PostgreSQL (`psycopg`).
  * `INTEGER PRIMARY KEY` diterjemahkan menjadi `BIGSERIAL PRIMARY KEY` di
    PostgreSQL sehingga id tetap dibuat otomatis oleh mesin.
  * `INSERT ... ON CONFLICT DO NOTHING` dipakai untuk idempotensi â€” didukung
    SQLite 3.24+ dan PostgreSQL.
  * `lastrowid` diemulasikan di PostgreSQL memakai `RETURNING id`.

API ini sengaja kecil dan eksplisit (tanpa ORM) agar mudah diaudit.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

from app import config

try:  # pragma: no cover - hanya relevan bila PostgreSQL dipakai
    import psycopg  # type: ignore
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore


def now_iso() -> str:
    """Waktu lokal (ISO-8601 detik) â€” format sama dengan V1 agar konsisten."""
    return datetime.now().isoformat(timespec="seconds")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Wrapper tipis di atas SQLite / PostgreSQL."""

    def __init__(self, url: str = "", path: Optional[Path] = None) -> None:
        self.url = url or config.DATABASE_URL
        self.path = Path(path or config.DATABASE_PATH)
        self.dialect = "postgresql" if self.url.startswith("postgres") else "sqlite"

    # -- koneksi ---------------------------------------------------------
    def connect(self):
        if self.dialect == "postgresql":
            if psycopg is None:
                raise RuntimeError(
                    "DATABASE_URL menunjuk PostgreSQL tetapi paket 'psycopg' tidak "
                    "terpasang. Pasang `pip install \"psycopg[binary]\"` di venv V2."
                )
            conn = psycopg.connect(self.url)
            conn.autocommit = False
            return conn
        config.ensure_dirs()
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    # -- adaptasi SQL ----------------------------------------------------
    def _adapt(self, sql: str) -> str:
        if self.dialect != "postgresql":
            return sql
        sql = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\b", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
        # placeholder qmark -> format psycopg
        return sql.replace("?", "%s")

    # -- eksekusi --------------------------------------------------------
    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(self._adapt(sql), tuple(params))
            rows = cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute(self._adapt(sql), tuple(params))
            row = cur.fetchone()
            return self._row_to_dict(row) if row is not None else None

    def query_value(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if not row:
            return default
        value = list(row.values())[0]
        return default if value is None else value

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Jalankan statement tulis. Mengembalikan id baris baru (bila applicable)."""
        with self.connection() as conn:
            cur = conn.cursor()
            adapted = self._adapt(sql)
            if self.dialect == "postgresql" and adapted.lstrip().upper().startswith("INSERT"):
                adapted = adapted.rstrip().rstrip(";") + " RETURNING id"
            cur.execute(adapted, tuple(params))
            new_id: Optional[int] = None
            if self.dialect == "postgresql" and adapted.rstrip().upper().endswith("RETURNING ID"):
                row = cur.fetchone()
                new_id = int(row[0]) if row else None
            elif self.dialect == "sqlite":
                new_id = int(cur.lastrowid or 0)
            conn.commit()
            return new_id or 0

    def execute_many(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        rows = [tuple(r) for r in seq]
        if not rows:
            return
        with self.connection() as conn:
            cur = conn.cursor()
            cur.executemany(self._adapt(sql), rows)
            conn.commit()

    # -- transaksi eksklusif ---------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Transaksi tulis atomik lintas koneksi.

        SQLite: `BEGIN IMMEDIATE` (mengunci database sejak awal) sehingga
        pemeriksaan "sudah ada / belum ada" tidak suffers race.
        PostgreSQL: `SERIALIZABLE` via `SELECT ... FOR UPDATE` pada baris kunci.
        """
        conn = self.connect()
        try:
            if self.dialect == "postgresql":
                conn.autocommit = False
                cur = conn.cursor()
                cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            else:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- utilitas ---------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: Any) -> dict:
        if row is None:
            return {}
        if isinstance(row, dict):
            return dict(row)
        if isinstance(row, sqlite3.Row):
            return {k: row[k] for k in row.keys()}
        try:  # psycopg tuples
            return dict(row)
        except Exception:
            return {"value": row}


# Instance tunggal aplikasi.
db = Database()


def init_db() -> None:
    """Buat skema bila belum ada (idempoten)."""
    from app.db import schema as schema_module

    config.ensure_dirs()
    with db.connection() as conn:
        cur = conn.cursor()
        for statement in _split_statements(schema_module.SCHEMA):
            cur.execute(db._adapt(statement))
        cur.execute(db._adapt("SELECT COUNT(*) FROM schema_version WHERE version = ?"),
                    (schema_module.SCHEMA_VERSION,))
        row = cur.fetchone()
        already = bool(row and (list(row.values())[0] if isinstance(row, dict) else row[0]))
        if not already:
            cur.execute(
                db._adapt("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)"),
                (schema_module.SCHEMA_VERSION, now_iso()),
            )
        conn.commit()


def _split_statements(sql: str) -> list[str]:
    """Pecah skrip SQL menjadi statement tunggal (abaikan comment & titik koma)."""
    statements: list[str] = []
    current: list[str] = []
    for raw_line in sql.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        current.append(raw_line)
        if line.endswith(";"):
            chunk = "\n".join(current).strip().rstrip(";")
            if chunk.strip():
                statements.append(chunk)
            current = []
    tail = "\n".join(current).strip().rstrip(";")
    if tail.strip():
        statements.append(tail)
    return statements


def audit(
    user_id: Optional[int],
    aksi: str,
    entitas: Optional[str] = None,
    entitas_id: Optional[int] = None,
    detail: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    """Catat aktivitas ke tabel audit_log (best effort, tidak boleh menggagalkan aksi utama)."""
    try:
        db.execute(
            """INSERT INTO audit_log (user_id, aksi, entitas, entitas_id, detail, ip, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, aksi, entitas, entitas_id, detail, ip, now_iso()),
        )
    except Exception:
        pass


def count_admins() -> int:
    try:
        return int(db.query_value(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND aktif = 1", (), 0
        ))
    except Exception:
        return 0


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        return db.query_value("SELECT value FROM pengaturan WHERE key = ?", (key,), default)
    except Exception:
        return default


def set_setting(key: str, value: Optional[str]) -> None:
    db.execute(
        """INSERT INTO pengaturan (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, value, now_iso()),
    )

