"""
Batch writer for search-count updates.

Instead of one SQLite write per search, submissions are buffered in memory and
aggregated by query. A background thread flushes the buffer to the store either
when it reaches BATCH_SIZE entries or every BATCH_INTERVAL_SECONDS — whichever
comes first. Repeated queries collapse into a single `+N` update, so 1000
searches for "iphone" become one row write, not 1000.

Failure trade-off: the buffer is in memory. If the process
crashes between flushes, the un-flushed deltas are lost — the durable count is
"behind" by at most one batch. We accept this because (a) the data is popularity
counts where small losses are harmless, and (b) flush-on-shutdown plus a short
interval bound the exposure. A write-ahead log would make it durable at the cost
of complexity; that trade-off is discussed in the README.
"""
import threading
import time
from collections import defaultdict

from .config import BATCH_INTERVAL_SECONDS, BATCH_SIZE, NEW_QUERY_INITIAL_COUNT
from .metrics import metrics


class BatchWriter:
    def __init__(self, store, batch_size: int = BATCH_SIZE,
                 interval: float = BATCH_INTERVAL_SECONDS):
        self.store = store
        self.batch_size = batch_size
        self.interval = interval
        self._buffer = defaultdict(int)     # query -> accumulated delta
        self._lock = threading.Lock()
        self._buffered_total = 0            # raw submissions held (for logging)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="batch-writer",
                                        daemon=True)

    def start(self):
        self._thread.start()

    def add(self, query: str):
        """Record one search submission (does not touch the DB)."""
        with self._lock:
            self._buffer[query] += 1
            self._buffered_total += 1
            should_flush = len(self._buffer) >= self.batch_size
        metrics.record_search(1)
        if should_flush:
            self.flush()

    def flush(self) -> int:
        """Aggregate the buffer and write it to the store in one transaction."""
        with self._lock:
            if not self._buffer:
                return 0
            snapshot = dict(self._buffer)
            self._buffer.clear()
            self._buffered_total = 0
        # apply_batch handles both inserts (new queries start at their delta,
        # which is >= NEW_QUERY_INITIAL_COUNT) and increments of existing rows.
        written = self.store.apply_batch(snapshot, ts=time.time())
        return written

    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.flush()
            except Exception as exc:  # keep the thread alive on transient errors
                print(f"[batch-writer] flush error: {exc}")

    def stop(self):
        """Stop the timer and flush whatever remains (called on shutdown)."""
        self._stop.set()
        self._thread.join(timeout=5)
        self.flush()


# NEW_QUERY_INITIAL_COUNT is re-exported for callers that want the documented
# initial count for a brand-new query.
__all__ = ["BatchWriter", "NEW_QUERY_INITIAL_COUNT"]
