"""
Ingest a `query,count` CSV into the SQLite primary store.

The trie is NOT built here — the app rebuilds it from SQLite on startup, so the
DB is the single source of truth. Run this once after generating/loading a
dataset; re-running replaces existing rows.

Usage:
    python -m scripts.ingest                      # reads data/queries.csv
    python -m scripts.ingest --csv data/queries.csv
"""
import argparse
import csv
import os
import sys

from app import config
from app.store import Store


def main():
    ap = argparse.ArgumentParser(description="Load queries.csv into SQLite.")
    ap.add_argument("--csv", default=config.DATASET_CSV, help="input CSV path")
    ap.add_argument("--db", default=config.DB_PATH, help="output SQLite path")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        sys.exit(f"CSV not found: {args.csv}\n"
                 f"Generate one first:  python -m scripts.generate_dataset")

    store = Store(args.db)
    batch, total = [], 0
    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            query = (row.get("query") or "").strip().lower()
            try:
                count = int(row.get("count", 0))
            except ValueError:
                continue
            if not query:
                continue
            batch.append((query, count))
            if len(batch) >= 10_000:        # commit in chunks to bound memory
                store.bulk_load(batch)
                total += len(batch)
                batch.clear()
    if batch:
        store.bulk_load(batch)
        total += len(batch)

    print(f"Ingested {total} rows into {args.db}")
    print(f"Total queries in store: {store.total_queries()}")
    store.close()


if __name__ == "__main__":
    main()
