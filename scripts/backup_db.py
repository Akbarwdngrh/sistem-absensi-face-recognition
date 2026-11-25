"""Pencadangan & pemulihan database V2.

Cadangan memakai ``VACUUM INTO`` (SQLite) sehingga menghasilkan salinan yang
konsisten tanpa menghentikan aplikasi.

Jalankan:
    .\\.venv\\Scripts\\python.exe -m scripts.backup_db
    .\\.venv\\Scripts\\python.exe -m scripts.backup_db --list
    .\\.venv\\Scripts\\python.exe -m scripts.backup_db --restore data\\backup_xxx.db
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.db.engine import db  # noqa: E402

BACKUP_DIR = config.DATA_DIR / "backups"


def list_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("absensi_v2_*.db"), reverse=True)


def make_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"absensi_v2_{stamp}.db"
    if db.dialect == "sqlite":
        conn = db.connect()
        try:
            conn.execute("VACUUM INTO ?", (str(target),))
        finally:
            conn.close()
    else:
        raise RuntimeError(
            "Cadangan otomatis untuk PostgreSQL belum didukung; gunakan pg_dump."
        )
    return target


def restore(backup: Path) -> None:
    if not backup.exists():
        raise FileNotFoundError(backup)
    target = Path(config.DATABASE_PATH)
    if target.exists():
        stash = BACKUP_DIR / f"pre_restore_{datetime.now():%Y%m%d_%H%M%S}.db"
        shutil.copy2(target, stash)
        print(f"Salinan database lama: {stash}")
    shutil.copy2(backup, target)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(target) + suffix)
        if extra.exists():
            extra.unlink()
    print(f"Database dipulihkan dari {backup}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cadangan/pemulihan database V2")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", default="", help="Path file cadangan untuk dipulihkan.")
    args = ap.parse_args(argv)

    if args.restore:
        restore(Path(args.restore))
        return 0

    if args.list:
        files = list_backups()
        if not files:
            print("Belum ada cadangan.")
        for f in files:
            print(f"  {f}  ({f.stat().st_size} byte)")
        return 0

    path = make_backup()
    print(f"Cadangan dibuat: {path} ({path.stat().st_size} byte)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

