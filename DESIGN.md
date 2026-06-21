# Design Notes

This document explains *why* the system is built the way it is — the reasoning
behind each component, the alternatives that were considered, and the trade-offs
that were accepted. The [README](README.md) covers how to run it and what the
APIs do; this is about the engineering decisions.

---

## 1. The core tension

A typeahead service has two workloads that pull in opposite directions:

- **Reads (`/suggest`)** happen on nearly every keystroke. They must be
  single-digit-millisecond and they vastly outnumber writes. They want data that
  is in RAM, precomputed, and never sorted or scanned at request time.
- **Writes (`/search`)** mutate the popularity counts that reads depend on. Done
  naively, every search is a disk write *and* invalidates read caches — so a
  burst of writes (exactly when something is trending) degrades read latency at
  the worst possible moment.

Almost every decision below is one of three answers to this tension:

1. **Precompute the reads** — the trie stores each node's top-N completions, so
   a read is "walk to the node, return its list".
2. **Cache in front, and distribute it** — most reads never reach the trie, and
   the cache can scale horizontally by consistent hashing.
3. **Decouple writes from disk** — searches update RAM instantly (reads stay
   fresh) while the durable write is buffered and batched in the background.

Recency ranking adds a fourth pressure: rankings now change continuously, so
cache freshness has to be managed actively, not just by expiry.

---

## 2. Storage and serving structure

### SQLite as the source of truth
**Decision:** a single SQLite file (`queries(query PRIMARY KEY, count, last_searched)`)
in WAL mode is the durable store.

**Why:** zero setup, ships with Python, single file, transactional, and trivial
to reason about. WAL mode lets the background writer commit while reads proceed.
The data model is tiny — popularity counts keyed by query string — so a
relational engine is more than enough and costs nothing to operate.

**Rejected alternatives:**
- *A full RDBMS (Postgres/MySQL):* operational overhead with no payoff at this
  scale. The access pattern is a keyed upsert and a full scan on startup —
  nothing that needs a server process.
- *A key-value store (Redis/RocksDB) as the primary store:* would conflate the
  cache layer with the source of truth, and Redis as the *only* store trades
  durability guarantees for speed we already get from the in-memory trie.

**Trade-off accepted:** SQLite is single-writer. That's fine here because all
writes funnel through one background batch writer anyway (see §5), so there's no
write contention to begin with.

### A prefix trie as the in-memory serving structure
**Decision:** an in-memory trie where **each node caches the top-N completions
(by count) of the subtree beneath it**.

**Why:** prefix matching is *the* operation, and a trie reaches a prefix in
`O(len(prefix))` regardless of how many queries exist. The twist that makes it
fast at serving time is the per-node top-N cache: without it, answering a prefix
means traversing its entire subtree and sorting; with it, the answer is already
sitting on the node. Serving becomes "walk to node, slice its list".

The cap is `CANDIDATE_POOL = 25` per node — larger than the 10 we return so that
recency re-ranking has runners-up to promote without re-scanning the subtree.

**Rejected alternatives:**
- *Scan + sort on each request:* simple but O(matches · log) per read — far too
  slow for the read volume, and worst on short, popular prefixes that match the
  most queries.
- *Precompute top-10 for every possible prefix into a hash map:* O(1) reads, but
  enormous memory and an expensive rebuild on every write. The trie shares
  storage across prefixes (common prefixes share nodes) and updates incrementally.
- *A database `LIKE 'prefix%'` query with an index:* works, but every read hits
  the DB and pays query-planning + sorting cost. The whole point is to keep reads
  off disk.

**Trade-off accepted:** the working set lives in RAM (~120k queries is small) and
the trie is rebuilt from SQLite on startup. The trie is a fast, rebuildable cache
of the durable store — losing it costs a few seconds of startup, not data.

**Key implementation detail — `_walk_and_offer`:** on insert/increment we walk
the query's path from the root and refresh the top-N list at *every* node along
it (the root represents the empty prefix). `_offer` removes any stale entry for
the query, then binary-inserts the new `[count, query]` in descending order and
trims to the cap. Counts only ever rise, so a single pass keeps every affected
node's list correct.

---

## 3. Distributed cache and consistent hashing

### Why a cache in front of the trie
Even though the trie is fast, the cache exists because (a) it makes the read path
**O(1)** for hot prefixes — a dictionary lookup instead of a tree walk — and
(b) it models a real distributed cache so the system can scale reads horizontally.
A cached value is the fully-formed top-10 list for a prefix.

### Why multiple logical nodes instead of one map
**Decision:** several in-process `CacheNode` objects, each an independent
TTL + LRU map, with a consistent-hashing ring deciding which node owns a prefix.

**Why in-process and not Redis:** the routing logic — which node owns a key, how
ownership rebalances when nodes change — is *identical* whether the node is a
local object or a remote server. Only the transport differs (a function call vs a
network hop). Keeping nodes in-process means the system runs anywhere with no
external dependency, while the part that actually matters (the ring) is real. A
`CacheNode` could be swapped for a Redis client without touching the ring.

**Trade-off accepted:** in-process nodes don't give true horizontal scaling or
shared cache across processes. That's a transport change, not a design change —
the ownership model already assumes independent nodes.

### Consistent hashing with virtual nodes
**Decision:** `consistent_hash.py` places each physical node on a circular
keyspace `VIRTUAL_NODES = 150` times (hashing `"node#i"` with md5). A key is owned
by the first node clockwise, found by `bisect` over the sorted ring positions.

**Why not `hash(key) % N`:** modulo hashing remaps **almost every key** when the
node count changes (the demo shows ~73% remapped going 3→4 nodes). Consistent
hashing only moves the keys between the changed node and its neighbour — roughly
`1/N` (the demo shows ~24%). For a cache, "remapped" means "cold miss", so modulo
hashing would cause a near-total cache wipe on any topology change.

**Why virtual nodes:** with one point per physical node, the random arcs between
points are very uneven, so one node can own far more keys than another. Hashing
each node to 150 points averages those arcs out — the demo shows a ~34/34/32%
spread across three nodes. More replicas = smoother distribution and smoother
rebalancing, at the cost of a bigger sorted ring (cheap: a few hundred ints).

**Why md5:** it just needs to be a stable, well-distributed hash. It isn't
security-sensitive, so a fast non-cryptographic hash would also be fine; md5
ships with Python and is deterministic across runs.

### Expiry *and* invalidation
Two mechanisms keep cached rankings from going stale:

- **TTL (30 s):** every entry expires on a timer; expired reads are treated as
  misses and recomputed. This bounds staleness even if nothing else happens.
- **Targeted invalidation:** when a search changes a query's count,
  `invalidate_prefixes()` deletes the cached list for *every prefix of that
  query* on its owning node. Each prefix may hash to a different node, so each is
  routed through the ring individually.

**Why both:** TTL alone could serve a stale ranking for up to 30 s after a spike;
invalidation alone could leak entries that no write ever touches again. Together,
freshness is immediate on change and bounded otherwise.

---

## 4. Recency-aware ranking

The basic ranking sorts purely by all-time count. The enhanced ranking blends in
recent activity so a query trending *now* can rise above a historically popular
but currently quiet one.

### How recent activity is tracked
**Decision:** each query keeps a single number — a **time-decayed score**. A
search adds `1.0` to it, but the existing value is first decayed by how much time
has passed:

```
decay over elapsed t  =  exp(-lambda * t),   lambda = ln(2) / half_life
```

This is an exponential moving sum with a half-life (30 min). The decay is applied
lazily, only when a query is touched or read, so there's no background sweep.

**Why this instead of fixed time-window buckets:** counting "searches in the last
N minutes" needs either per-event timestamps or rotating buckets, plus a cleanup
job. The decayed score captures the same "recent vs old" intuition in **one float
per query**, updated in O(1), with no bookkeeping. Older activity fades smoothly
instead of dropping off a cliff at a window boundary.

### How it affects ranking
```
effective_score(q) = all_time_count(q) + RECENCY_BOOST * recent_score(q)
```

`RECENCY_BOOST = 5000` converts "decayed recent searches" into "equivalent
all-time counts" so the two quantities live on one scale and can simply be added.
A query with a strong recent burst gains thousands of effective counts and climbs.

### Why short-lived spikes don't dominate forever
The recent score **decays continuously toward zero**. Once a query stops being
searched, its boost melts away over a few half-lives and it falls back to its
all-time rank. Nothing can be permanently stuck at the top — the only way to stay
boosted is to keep being searched.

### How the cache stays correct under changing rankings
Recency mode pairs a **short TTL** with **targeted invalidation on every search**
(§3). Because a search both changes the ranking and invalidates the affected
prefixes, the next read recomputes against the fresh score.

### Trade-offs
- Shorter TTL / more invalidation → fresher rankings but a lower cache hit rate.
- A single decayed score is cheap and easy to explain but **approximate** — it
  can't answer "exactly how many searches in the last 10 minutes". For ranking,
  approximate-but-cheap is the right call; exact windowed counts would cost far
  more memory and complexity for no ranking benefit.

The same `/suggest` endpoint serves both modes via `RANKING_MODE`, so the
difference is a config flag, not a code path the caller has to know about.

---

## 5. Batch writes

**Decision:** `/search` never writes to SQLite synchronously. It increments the
trie in memory and **buffers** the count delta. A background daemon thread flushes
the buffer to SQLite when it reaches `BATCH_SIZE` (200 distinct queries) **or**
every `BATCH_INTERVAL_SECONDS` (5 s), whichever comes first.

**Why:** synchronous per-search writes make the write path's throughput hostage to
disk latency, and contend with everything else. Buffering does two things:

- **Aggregation:** repeated searches for the same query collapse into a single
  `+N` upsert. 1000 searches for "iphone" become one row write, not 1000. The
  buffer is a `query → accumulated delta` map, so this is automatic.
- **Amortization:** one transaction commits many distinct updates, so per-write
  fixed costs (transaction overhead, fsync) are shared.

The result is the measured ~98.6% write reduction — and crucially, reads never
wait on a write, because the trie was already updated in memory.

**The upsert** uses `INSERT ... ON CONFLICT(query) DO UPDATE SET count = count +
excluded.count`, so new queries are inserted at their delta and existing ones are
incremented, all in one statement.

**Failure trade-off (the important one):** the buffer is in memory. If the process
crashes between flushes, the un-flushed deltas are lost — the durable count lags
by at most one batch (≤ 5 s or ≤ 200 distinct queries of activity). This is
accepted because:

1. The data is popularity counters, where losing a few increments is harmless —
   it slightly under-counts, it never corrupts.
2. Exposure is bounded by a short interval and by **flushing on shutdown** for
   graceful stops.

A write-ahead log or an append-only queue would make this durable, at the cost of
extra I/O and complexity. For popularity counts that trade isn't worth it; for
something like billing it absolutely would be, and the buffer would be replaced by
a durable log before the aggregation step.

---

## 6. Concurrency model

- The **trie** is guarded by an `RLock`; reads and the (in-memory) increment all
  take it. Lookups are short (slice a list), so contention is low.
- Each **cache node** has its own lock, so traffic to different nodes doesn't
  contend — another reason the ring matters beyond scaling.
- **SQLite** uses one shared connection with `check_same_thread=False` plus a
  lock, so the background writer can reuse it safely. Since all writes go through
  the single batch writer, there's effectively one writer and no write contention.
- **Metrics** are counters behind a lock, updated off the hot path.

---

## 7. Scalability — ceilings and bottlenecks

What this design handles well, and where it would break first:

| Dimension | Current behavior | First bottleneck |
|-----------|------------------|------------------|
| Read QPS (hot prefixes) | O(1) cache hits, sub-ms | Single Python process / GIL; one box's CPU |
| Read QPS (cold prefixes) | O(len(prefix)) trie walk | Trie lock under heavy concurrent misses |
| Dataset size | Whole trie in RAM | RAM; rebuild time on startup grows linearly |
| Write QPS | Buffered + aggregated | SQLite single-writer once batches get large/frequent |
| Cache capacity | Per-node LRU cap | In-process memory; no sharing across processes |

The honest summary: this is a **single-process** design. It's fast and correct,
but it scales *up* (one bigger box), not *out*, without the changes below.

---

## 8. What would change for production scale

In rough priority order:

1. **Move the cache out of process.** Swap each `CacheNode` for a Redis instance.
   The consistent-hashing ring already models exactly this — clients route the
   same way; only the transport changes. This is the single biggest unlock and
   the design was built to make it a drop-in.
2. **Shard the trie.** Partition by first character (or a hash of the prefix)
   across processes/nodes so no single box holds the whole keyspace, and so
   rebuild time and RAM scale horizontally.
3. **Make writes durable before aggregating.** Replace the in-memory buffer with
   an append-only log (or Kafka). Aggregate off the log, so a crash replays
   instead of losing deltas.
4. **Persist the serving structure / speed up cold start.** Snapshot the trie
   periodically so startup loads a snapshot + replays recent writes instead of
   scanning the entire table.
5. **Promote the store** from SQLite to a horizontally-scalable store once a
   single writer is the bottleneck — but only then, because SQLite is carrying
   its weight today.
6. **Observability:** export metrics to Prometheus, add tracing on the read path,
   and alert on cache hit rate (the leading indicator of read latency).

The point of listing these is that none of them are rewrites — the boundaries
(store / trie / cache / writer) were drawn so each can be replaced independently.

---

## 9. Quick reference — decisions and rejected alternatives

| Component | Chosen | Rejected | Because |
|-----------|--------|----------|---------|
| Source of truth | SQLite (WAL) | Postgres / Redis-only | Zero-setup, durable, enough for a keyed-upsert workload |
| Serving structure | Trie with per-node top-N | Scan+sort / full prefix map | O(len(prefix)) reads, incremental updates, shared storage |
| Read cache | In-process logical nodes | Redis now / single map | Real routing model, runs anywhere; nodes split lock contention |
| Cache routing | Consistent hashing + vnodes | `hash % N` | Topology change remaps ~1/N keys, not ~all |
| Freshness | TTL + targeted invalidation | TTL only / invalidation only | Immediate on change, bounded otherwise |
| Recency | Decayed score (one float/query) | Time-window buckets | O(1), no cleanup, smooth fade, no cliff |
| Writes | Buffer + aggregate + batch flush | Synchronous per-search write | ~99% fewer writes; reads never wait on disk |
| Write durability | In-memory buffer + flush on shutdown | Write-ahead log | Popularity counts tolerate ≤1-batch loss; WAL not worth the cost |
