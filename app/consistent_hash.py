"""
Consistent-hashing ring with virtual nodes.

Plain hashing (node = hash(key) % N) remaps almost every key when N changes.
Consistent hashing places nodes and keys on a fixed circular keyspace; a key is
owned by the first node found clockwise. Adding/removing a node only moves the
keys between that node and its neighbour — roughly 1/N of all keys.

Virtual nodes: each physical node is hashed onto the ring `virtual_nodes` times.
This evens out the distribution (a single point per node would leave large,
uneven arcs) and makes rebalancing smoother.
"""
import bisect
import hashlib


class ConsistentHashRing:
    def __init__(self, nodes=None, virtual_nodes: int = 150):
        self.virtual_nodes = virtual_nodes
        self._ring = {}        # ring position (int) -> physical node name
        self._sorted = []      # sorted ring positions, for clockwise lookup
        self._nodes = set()
        for node in nodes or []:
            self.add_node(node)

    @staticmethod
    def _hash(key: str) -> int:
        """Stable 128-bit hash as an int. md5 is fine here (not security-sensitive)."""
        return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)

    def add_node(self, node: str):
        if node in self._nodes:
            return
        self._nodes.add(node)
        for i in range(self.virtual_nodes):
            pos = self._hash(f"{node}#{i}")
            self._ring[pos] = node
            bisect.insort(self._sorted, pos)

    def remove_node(self, node: str):
        if node not in self._nodes:
            return
        self._nodes.discard(node)
        for i in range(self.virtual_nodes):
            pos = self._hash(f"{node}#{i}")
            self._ring.pop(pos, None)
            idx = bisect.bisect_left(self._sorted, pos)
            if idx < len(self._sorted) and self._sorted[idx] == pos:
                self._sorted.pop(idx)

    def get_node(self, key: str):
        """Return the node that owns `key` (first node clockwise)."""
        if not self._sorted:
            return None
        pos = self._hash(key)
        idx = bisect.bisect_right(self._sorted, pos)
        if idx == len(self._sorted):
            idx = 0                     # wrap around the ring
        return self._ring[self._sorted[idx]]

    def describe(self, key: str) -> dict:
        """Routing detail for a key — used by the /cache/debug endpoint."""
        if not self._sorted:
            return {"key": key, "node": None}
        pos = self._hash(key)
        idx = bisect.bisect_right(self._sorted, pos)
        if idx == len(self._sorted):
            idx = 0
        ring_pos = self._sorted[idx]
        return {
            "key": key,
            "key_hash": pos,
            "owner_node": self._ring[ring_pos],
            "ring_position": ring_pos,
            "virtual_nodes_per_node": self.virtual_nodes,
        }

    def nodes(self):
        return sorted(self._nodes)

    def distribution(self, keys) -> dict:
        """Count how many of `keys` land on each node (for the hashing demo)."""
        counts = {n: 0 for n in self._nodes}
        for k in keys:
            node = self.get_node(k)
            if node is not None:
                counts[node] += 1
        return counts
