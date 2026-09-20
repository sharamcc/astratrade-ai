#!/usr/bin/env python3
"""A single-process scheduler for simulation-only Agent runs."""

from __future__ import annotations

import os
import sqlite3
import time

from astratrade.console_store import ConsoleStore
from astratrade.simulation import run_due


def main() -> None:
    store = ConsoleStore(sqlite3.connect(os.environ.get("ASTRA_DB_PATH", "/var/lib/astratrade-ai/app.sqlite3"), timeout=30))
    while True:
        try:
            run_due(store)
        except Exception as error:  # keep the worker alive and observable
            store.audit(None, "worker_error", {"error": str(error)})
            store.connection.commit()
        time.sleep(int(os.environ.get("ASTRA_WORKER_INTERVAL", "10")))


if __name__ == "__main__":
    main()
