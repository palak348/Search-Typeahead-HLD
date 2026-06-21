"""
Lightweight, thread-safe metrics.

Backs the performance report: suggestion latency (incl. p95), cache hit rate,
and database read/write counts. Everything
is in-memory and resettable so a benchmark run starts from a clean slate.
"""
import threading
from collections import deque


class Metrics:
    def __init__(self, latency_window: int = 5000):
        self._lock = threading.Lock()
        # Ring buffer of recent /suggest latencies in milliseconds.
        self._latencies = deque(maxlen=latency_window)
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_reads = 0        # rows/queries read from SQLite
        self.db_writes = 0       # rows written to SQLite (via batches)
        self.flush_count = 0     # number of batch flushes performed
        self.search_count = 0    # /search submissions received
        self.suggest_count = 0   # /suggest requests served

    # --- recording -----------------------------------------------------------
    def record_latency(self, ms: float):
        with self._lock:
            self._latencies.append(ms)
            self.suggest_count += 1

    def record_cache(self, hit: bool):
        with self._lock:
            if hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    def record_db_read(self, n: int = 1):
        with self._lock:
            self.db_reads += n

    def record_db_write(self, rows: int):
        with self._lock:
            self.db_writes += rows
            self.flush_count += 1

    def record_search(self, n: int = 1):
        with self._lock:
            self.search_count += n

    # --- reporting -----------------------------------------------------------
    def _percentile(self, data, pct: float) -> float:
        if not data:
            return 0.0
        ordered = sorted(data)
        # nearest-rank percentile
        k = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * len(ordered) + 0.5)) - 1))
        return ordered[k]

    def snapshot(self) -> dict:
        with self._lock:
            lat = list(self._latencies)
            total_cache = self.cache_hits + self.cache_misses
            hit_rate = (self.cache_hits / total_cache) if total_cache else 0.0
            # Write reduction: searches received vs rows actually written to DB.
            write_reduction = (
                1.0 - (self.db_writes / self.search_count)
                if self.search_count else 0.0
            )
            return {
                "suggest": {
                    "count": self.suggest_count,
                    "latency_ms": {
                        "p50": round(self._percentile(lat, 50), 3),
                        "p95": round(self._percentile(lat, 95), 3),
                        "p99": round(self._percentile(lat, 99), 3),
                        "avg": round(sum(lat) / len(lat), 3) if lat else 0.0,
                        "samples": len(lat),
                    },
                },
                "cache": {
                    "hits": self.cache_hits,
                    "misses": self.cache_misses,
                    "hit_rate": round(hit_rate, 4),
                },
                "database": {
                    "reads": self.db_reads,
                    "writes": self.db_writes,
                    "flushes": self.flush_count,
                },
                "batching": {
                    "searches_received": self.search_count,
                    "db_rows_written": self.db_writes,
                    "write_reduction": round(write_reduction, 4),
                },
            }

    def reset(self):
        with self._lock:
            self._latencies.clear()
            self.cache_hits = self.cache_misses = 0
            self.db_reads = self.db_writes = self.flush_count = 0
            self.search_count = self.suggest_count = 0


# Single shared instance for the whole app.
metrics = Metrics()
