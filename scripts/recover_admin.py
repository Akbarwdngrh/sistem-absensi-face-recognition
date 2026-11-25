"""Pemulihan kata sandi admin V2 (lokal, interaktif, tidak menghapus data).

Jalankan:
    .\\.venv\\Scripts\\python.exe -m scripts.recover_admin --list
    .\\.venv\\Scripts\\python.exe -m scripts.recover_admin --username admin
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.core import security  # noqa: E402
from app.db.engine import audit, db, init_db, now_iso  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pemulihan kata sandi admin V2")
    ap.add_argument("--list", action="store_true", help="Tampilkan daftar akun admin.")
    ap.add_argument("--username", default="", help="Username admin yang akan disetel ulang.")
    args = ap.parse_args(argv)

    init_db()

    if args.list or not args.username:
        rows = db.query_all(
            "SELECT id, username, nama, aktif, created_at FROM users WHERE role='admin' ORDER BY id"
        )
        if not rows:
            print("Belum ada akun admin. Jalankan aplikasi lalu buka /setup.")
            return 1
        print("Akun admin:")
        for r in rows:
            print(f"  #{r['id']} {r['username']} â€” {r['nama']} "
                  f"({'aktif' if r['aktif'] else 'nonaktif'}) {r['created_at']}")
        if not args.username:
            return 0

    row = db.query_one(
        "SELECT id, username, nama FROM users WHERE role='admin' AND lower(username)=lower(?)",
        (args.username.strip(),),
    )
    if row is None:
        print("Akun admin tidak ditemukan.")
        return 1

    password = getpass.getpass("Kata sandi baru (tidak ditampilkan): ")
    confirm = getpass.getpass("Ulangi kata sandi: ")
    if password != confirm:
        print("Konfirmasi tidak cocok.")
        return 2
    err = security.validate_password(password, config.PASSWORD_MIN_LENGTH,
                                     username=row["username"], nama=row["nama"])
    if err:
        print(f"Kata sandi ditolak: {err}")
        return 2

    with db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (security.hash_password(password), now_iso(), row["id"]),
        )
        cur.execute("DELETE FROM auth_sessions WHERE user_id = ?", (row["id"],))
        conn.commit()

    audit(None, "password_reset_cli", "users", row["id"], f"pemulihan admin {row['username']}")
    print(f"Kata sandi admin '{row['username']}' diperbarui. Sesi login lama diakhiri.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

