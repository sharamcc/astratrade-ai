#!/usr/bin/env python3
"""Create a consistent SQLite backup and retain a bounded history."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    source = Path(os.environ.get("ASTRA_DB_PATH", "/var/lib/astratrade-ai/app.sqlite3"))
    target_dir = Path(os.environ.get("ASTRA_BACKUP_DIR", "/var/backups/astratrade-ai"))
    retention = int(os.environ.get("ASTRA_BACKUP_RETENTION", "14"))
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"app-{stamp}.sqlite3"
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    target.chmod(0o600)
    backups = sorted(target_dir.glob("app-*.sqlite3"), reverse=True)
    for old in backups[retention:]:
        old.unlink()
    print(f"created {target}")


if __name__ == "__main__":
    main()
