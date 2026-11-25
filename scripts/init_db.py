"""Inisialisasi database V2 (idempoten).

Jalankan:
    .\\.venv\\Scripts\\python.exe -m scripts.init_db
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.db.engine import db, init_db  # noqa: E402


def main() -> int:
    config.ensure_dirs()
    init_db()
    print("Skema database V2 siap:")
    print(f"  backend    : {db.dialect}")
    print(f"  lokasi     : {config.DATABASE_PATH if db.dialect == 'sqlite' else config.DATABASE_URL}")
    tables = db.query_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        if db.dialect == "sqlite" else
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema='public' ORDER BY table_name"
    )
    print(f"  tabel      : {', '.join(t['name'] for t in tables)}")
    print("\nLangkah berikutnya: jalankan aplikasi lalu buka http://127.0.0.1:8010/setup")
    print("untuk membuat akun admin pertama.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

