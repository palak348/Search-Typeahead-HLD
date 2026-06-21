"""
Distributed suggestion cache.

Layout: several *logical* cache nodes living in-process, each an independent
TTL + LRU key-value store. A consistent-hashing ring decides which node owns a
given prefix key, so the cache is "distributed" across nodes exactly as a real
multi-server cache would be — the routing logic is identical; only the transport
(a function call vs a network hop) differs.

Cached value for a prefix = the precomputed list of up to 10 suggestions.
Entries expire via TTL and can also be explicitly invalidated when a search
changes the ranking for a prefix.
"""
import threading
import time
from collections import OrderedDict

from .config import CACHE_MAX_ENTRIES, CACHE_NODES, CACHE_TTL_SECONDS, VIRTUAL_NODES
from .consistent_hash import ConsistentHashRing
from .metrics import metrics


class CacheNode:
    """One logical cache node: an LRU map of key -> (value, expiry_timestamp)."""

    def __init__(self, name: str, max_entries: int = CACHE_MAX_ENTRIES):
        self.name = name
        self.max_entries = max_entries
        self._store = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, expiry = entry
            if expiry < now:                 # expired -> treat as miss
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)     # mark most-recently-used
            self.hits += 1
            return value

    def set(self, key: str, value, ttl: float):
        with self._lock:
            self._store[key] = (value, time.time() + ttl)
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)   # evict least-recently-used

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def contains_live(self, key: str) -> bool:
        """True if key is present and unexpired (read-only; no LRU/stat change)."""
        with self._lock:
            entry = self._store.get(key)
            return entry is not None and entry[1] >= time.time()

    def stats(self) -> dict:
        with self._lock:
            return {"name": self.name, "size": len(self._store),
                    "hits": self.hits, "misses": self.misses}


class DistributedCache:
    def __init__(self, node_names=CACHE_NODES, ttl: float = CACHE_TTL_SECONDS):
        self.ttl = ttl
        self.ring = ConsistentHashRing(node_names, virtual_nodes=VIRTUAL_NODES)
        self._nodes = {name: CacheNode(name) for name in node_names}

    def _node_for(self, prefix: str) -> CacheNode:
        return self._nodes[self.ring.get_node(prefix)]

    def get(self, prefix: str):
        """Look up cached suggestions for a prefix. Records hit/miss in metrics."""
        value = self._node_for(prefix).get(prefix)
        metrics.record_cache(hit=value is not None)
        return value

    def set(self, prefix: str, suggestions):
        self._node_for(prefix).set(prefix, suggestions, self.ttl)

    def invalidate_prefixes(self, query: str):
        """
        Drop cached entries for every prefix of `query` on their owning nodes.
        Called after a search changes a query's count so stale ranked lists for
        the affected prefixes aren't served. Each prefix may live on a different
        node, so we route every prefix through the ring individually.
        """
        for i in range(1, len(query) + 1):
            prefix = query[:i]
            self._node_for(prefix).delete(prefix)

    # --- introspection for /cache/debug and /metrics -------------------------
    def debug(self, prefix: str) -> dict:
        detail = self.ring.describe(prefix)
        node = self._nodes.get(detail.get("owner_node"))
        hit = node.contains_live(prefix) if node else False
        detail["cache_status"] = "HIT" if hit else "MISS"
        return detail

    def node_stats(self):
        return [self._nodes[n].stats() for n in sorted(self._nodes)]
