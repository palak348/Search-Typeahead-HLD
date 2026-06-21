"""
Prefix trie for typeahead suggestions.

Why a trie: prefix lookup is the core operation, and a trie walks straight to
the node for a prefix in O(len(prefix)) regardless of how many queries exist.
To avoid scanning the whole subtree on every request, each node caches the
top-N completions (by all-time count) beneath it. Serving a prefix is then just
"walk to the node, read its cached list" — no subtree traversal at request time.

The trie is the in-memory *serving* structure. SQLite (see store.py) is the
durable source of truth; the trie is rebuilt from it on startup.
"""
import threading

from .config import CANDIDATE_POOL, MAX_SUGGESTIONS


class _Node:
    __slots__ = ("children", "top")

    def __init__(self):
        self.children = {}
        # Cached best completions under this node: list of [count, query],
        # sorted by count descending, length capped at CANDIDATE_POOL.
        self.top = []


class Trie:
    def __init__(self, candidate_pool: int = CANDIDATE_POOL):
        self.root = _Node()
        self.candidate_pool = candidate_pool
        self._counts = {}          # query -> current count (mirror of the store)
        self._lock = threading.RLock()

    # --- internal: maintain a node's cached top list -------------------------
    def _offer(self, node: _Node, query: str, count: int):
        """Insert/update (query, count) into node.top, keeping it sorted & capped."""
        top = node.top
        # Remove any stale entry for this query first (counts only ever rise).
        for i, (_, q) in enumerate(top):
            if q == query:
                top.pop(i)
                break
        # Skip if the list is full and this count can't make the cut.
        if len(top) >= self.candidate_pool and count <= top[-1][0]:
            return
        # Insert in sorted position (descending by count).
        lo, hi = 0, len(top)
        while lo < hi:
            mid = (lo + hi) // 2
            if top[mid][0] >= count:
                lo = mid + 1
            else:
                hi = mid
        top.insert(lo, [count, query])
        if len(top) > self.candidate_pool:
            top.pop()

    def _walk_and_offer(self, query: str, count: int):
        """Refresh the cached top list at every node along the query's path."""
        node = self.root
        self._offer(node, query, count)          # root == empty prefix
        for ch in query:
            nxt = node.children.get(ch)
            if nxt is None:
                nxt = _Node()
                node.children[ch] = nxt
            node = nxt
            self._offer(node, query, count)

    # --- public API ----------------------------------------------------------
    def insert(self, query: str, count: int):
        """Add a query with an absolute count (used during bulk load)."""
        with self._lock:
            self._counts[query] = count
            self._walk_and_offer(query, count)

    def increment(self, query: str, delta: int = 1, initial: int = 1) -> int:
        """
        Bump a query's count by `delta` (insert at `initial` if new).
        Updates the serving structure immediately so suggestions reflect the
        search right away, even though the durable DB write is batched.
        Returns the new count.
        """
        with self._lock:
            if query in self._counts:
                new = self._counts[query] + delta
            else:
                new = initial + (delta - 1 if delta > 0 else 0)
            self._counts[query] = new
            self._walk_and_offer(query, new)
            return new

    def _node_for(self, prefix: str):
        node = self.root
        for ch in prefix:
            node = node.children.get(ch)
            if node is None:
                return None
        return node

    def top_k(self, prefix: str, k: int = MAX_SUGGESTIONS):
        """Return up to k (query, count) completions for prefix, count-desc."""
        with self._lock:
            node = self._node_for(prefix)
            if node is None:
                return []
            return [(q, c) for c, q in node.top[:k]]

    def candidates(self, prefix: str):
        """Larger candidate pool for a prefix (used by recency re-ranking)."""
        with self._lock:
            node = self._node_for(prefix)
            if node is None:
                return []
            return [(q, c) for c, q in node.top]

    def count_of(self, query: str) -> int:
        return self._counts.get(query, 0)

    def size(self) -> int:
        return len(self._counts)
