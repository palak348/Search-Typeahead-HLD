"""
Consistent-hashing demonstration (no server needed).

Shows two things about the cache routing:

  1. Distribution: many keys spread roughly evenly across nodes thanks to
     virtual nodes.
  2. Rebalancing: when a node is added/removed, only ~1/N of keys change owner —
     the property that makes consistent hashing useful versus modulo hashing.

Run:
    python -m bench.hashing_demo
"""
from app.config import VIRTUAL_NODES
from app.consistent_hash import ConsistentHashRing


def make_keys(n: int):
    # Prefix-like keys (1-4 chars), similar to what the cache actually stores.
    import string
    letters = string.ascii_lowercase
    keys = []
    for a in letters:
        keys.append(a)
        for b in letters:
            keys.append(a + b)
            keys.append(a + b + b)
    return keys[:n]


def show_distribution(ring, keys):
    dist = ring.distribution(keys)
    total = sum(dist.values())
    print(f"  {'node':<16}{'keys':>8}{'share':>10}")
    for node in sorted(dist):
        c = dist[node]
        print(f"  {node:<16}{c:>8}{c / total * 100:>9.1f}%")


def main():
    keys = make_keys(2000)
    print(f"Keys: {len(keys)} | virtual nodes per physical node: {VIRTUAL_NODES}\n")

    ring = ConsistentHashRing(["cache-node-0", "cache-node-1", "cache-node-2"],
                              virtual_nodes=VIRTUAL_NODES)
    print("Distribution across 3 nodes:")
    show_distribution(ring, keys)

    # Record current ownership, then add a 4th node.
    before = {k: ring.get_node(k) for k in keys}
    ring.add_node("cache-node-3")
    after = {k: ring.get_node(k) for k in keys}
    moved = sum(1 for k in keys if before[k] != after[k])
    print("\nAfter ADDING cache-node-3:")
    show_distribution(ring, keys)
    print(f"  keys remapped: {moved}/{len(keys)} = {moved/len(keys)*100:.1f}% "
          f"(ideal ~{100/4:.0f}% - only the new node's share moves)")

    # Remove a node and measure churn again.
    before = after
    ring.remove_node("cache-node-1")
    after = {k: ring.get_node(k) for k in keys}
    moved = sum(1 for k in keys if before[k] != after[k])
    print("\nAfter REMOVING cache-node-1:")
    show_distribution(ring, keys)
    print(f"  keys remapped: {moved}/{len(keys)} = {moved/len(keys)*100:.1f}% "
          f"(only keys that lived on the removed node move)")

    # Contrast with naive modulo hashing, which remaps almost everything.
    import hashlib

    def mod_owner(key, n):
        h = int(hashlib.md5(key.encode()).hexdigest(), 16)
        return h % n

    moved_mod = sum(1 for k in keys if mod_owner(k, 3) != mod_owner(k, 4))
    print(f"\nFor comparison - naive hash % N when going 3 -> 4 nodes: "
          f"{moved_mod}/{len(keys)} = {moved_mod/len(keys)*100:.1f}% remapped.")
    print("Consistent hashing moves far fewer keys, which is the whole point.")


if __name__ == "__main__":
    main()
