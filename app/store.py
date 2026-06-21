"""
SQLite primary store — the durable source of truth for query counts.

Chosen because it is zero-setup, single-file, ships with Python, and is trivial
to explain. The in-memory trie is rebuilt from this table on startup, so the DB
is what survives a restart. Writes arrive here in *batches* (see batch_writer),
not one-per-search, which is the whole point of the batch-write requirement.
"""
import sqlite3
import threading

from .config import DB_PATH
from .metrics import metrics


class Store:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        # One shared connection guarded by a lock. check_same_thread=False lets
        # the background batch-writer thread reuse it; the lock serializes access.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")  # better concurrent read/write
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queries (
                    query         TEXT PRIMARY KEY,
                    count         INTEGER NOT NULL,
                    last_searched REAL
                )
                """
            )
            self._conn.commit()

    # --- bulk load (dataset ingestion) ---------------------------------------
    def bulk_load(self, rows):
        """Insert/replace many (query, count) rows in one transaction."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            cur.executemany(
                "INSERT OR REPLACE INTO queries(query, count, last_searched) "
                "VALUES (?, ?, NULL)",
                rows,
            )
            self._conn.commit()
            return cur.rowcount

    # --- batched search-count updates ----------------------------------------
    def apply_batch(self, deltas: dict, ts: float) -> int:
        """
        Apply aggregated count deltas in a single transaction.
        `deltas` maps query -> total increment accumulated since the last flush.
        New queries are inserted; existing ones are incremented.
        Returns the number of rows written (for write-reduction metrics).
        """
        if not deltas:
            return 0
        items = [(q, d, ts) for q, d in deltas.items()]
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            cur.executemany(
                """
                INSERT INTO queries(query, count, last_searched)
                VALUES (?, ?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    count = count + excluded.count,
                    last_searched = excluded.last_searched
                """,
                items,
            )
            self._conn.commit()
            written = len(items)
        metrics.record_db_write(written)
        return written

    # --- reads ---------------------------------------------------------------
    def iter_all(self):
        """Yield every (query, count) — used to rebuild the trie on startup."""
        with self._lock:
            cur = self._conn.execute("SELECT query, count FROM queries")
            rows = cur.fetchall()
        metrics.record_db_read(len(rows))
        yield from rows

    def get_count(self, query: str):
        with self._lock:
            cur = self._conn.execute(
                "SELECT count FROM queries WHERE query = ?", (query,)
            )
            row = cur.fetchone()
        metrics.record_db_read(1)
        return row[0] if row else None

    def total_queries(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM queries")
            return cur.fetchone()[0]

    def close(self):
        with self._lock:
            self._conn.close()
