#!/usr/bin/env python3
import sqlite3
import hashlib
import secrets
import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DATABASE_PATH", ROOT / "data" / "app.db"))


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main():
    code = f"ham-{secrets.token_urlsafe(8)}"
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO invite_codes (code_hash, created_at) VALUES (?, ?)",
            (hash_value(code), datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    print(code)


if __name__ == "__main__":
    main()
