#!/usr/bin/env python3
"""Create or promote the first console administrator without storing credentials in git."""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3

from astratrade.console_store import ConsoleStore
from astratrade.domain import User
from astratrade.repository import Repository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--db", default=os.environ.get("ASTRA_DB_PATH", "/var/lib/astratrade-ai/app.sqlite3"))
    args = parser.parse_args()
    password = getpass.getpass("Admin password (min 12 chars): ")
    store = ConsoleStore(sqlite3.connect(args.db, timeout=30))
    user = store.create_admin(args.email, password)
    repository = Repository(sqlite3.connect(args.db, timeout=30))
    repository.save_user(User(user["user_id"], user["email"], risk_confirmed=True))
    print(f"admin ready: {user['email']}")


if __name__ == "__main__":
    main()
