"""
FastAPI application — wires the data layer, cache, trending and batch writer
together and exposes the HTTP API.

Endpoints
    GET  /suggest?q=<prefix>      up to 10 prefix suggestions, count-sorted
    POST /search  {query}         dummy "Searched" response + records the query
    GET  /cache/debug?prefix=     which cache node owns a prefix + hit/miss
    GET  /trending                current trending queries
    GET  /metrics                 latency p95, cache hit rate, DB read/write counts
    GET  /                        the search UI
    GET  /docs                    auto-generated API docs (FastAPI)
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .batch_writer import BatchWriter
from .cache import DistributedCache
from .metrics import metrics
from .store import Store
from .trending import TrendingTracker
from .trie import Trie

# Shared singletons, populated in the lifespan handler.
state = {}


def _normalize(text: str) -> str:
    """Lower-case + trim so matching is case-insensitive."""
    return (text or "").strip().lower()


def _rank_suggestions(prefix: str):
    """
    Build the ranked suggestion list for a prefix.

    popularity mode: trie already holds completions sorted by all-time count.
    recency mode:    take the candidate pool, add any trending queries matching
                     the prefix, then re-sort by (count + recency boost).
    """
    trie: Trie = state["trie"]
    if config.RANKING_MODE != "recency":
        results = trie.top_k(prefix, config.MAX_SUGGESTIONS)
        return [{"query": q, "count": c, "score": c} for q, c in results]

    trending: TrendingTracker = state["trending"]
    now = time.time()
    candidates = dict(trie.candidates(prefix))          # query -> count
    for q in trending.prefix_matches(prefix):           # surface fresh spikes
        candidates.setdefault(q, trie.count_of(q))
    scored = [
        {"query": q, "count": c, "score": round(c + trending.boost_for(q, now), 2)}
        for q, c in candidates.items()
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:config.MAX_SUGGESTIONS]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup: open store, rebuild serving trie, start background writer ---
    store = Store()
    trie = Trie()
    loaded = 0
    for query, count in store.iter_all():
        trie.insert(query, count)
        loaded += 1
    print(f"[startup] loaded {loaded} queries from {config.DB_PATH} into the trie")

    cache = DistributedCache()
    trending = TrendingTracker()
    batch_writer = BatchWriter(store)
    batch_writer.start()

    state.update(store=store, trie=trie, cache=cache,
                 trending=trending, batch_writer=batch_writer)
    print(f"[startup] cache nodes: {cache.ring.nodes()} | ranking mode: "
          f"{config.RANKING_MODE}")
    try:
        yield
    finally:
        # --- shutdown: flush buffered searches so they aren't lost ------------
        print("[shutdown] flushing batch writer ...")
        batch_writer.stop()
        store.close()


app = FastAPI(title="Search Typeahead System", version="1.0", lifespan=lifespan)


# --- API ---------------------------------------------------------------------
@app.get("/suggest")
def suggest(q: str = Query("", description="prefix to complete")):
    """Return up to 10 suggestions for a prefix, sorted by ranking score."""
    started = time.perf_counter()
    prefix = _normalize(q)

    # Empty/missing input -> empty list, gracefully (not an error).
    if not prefix:
        metrics.record_latency((time.perf_counter() - started) * 1000)
        return {"query": q, "suggestions": [], "source": "empty"}

    cache: DistributedCache = state["cache"]
    cached = cache.get(prefix)
    if cached is not None:
        metrics.record_latency((time.perf_counter() - started) * 1000)
        return {"query": q, "suggestions": cached, "source": "cache"}

    # Cache miss: compute from the trie, then populate the owning cache node.
    suggestions = _rank_suggestions(prefix)
    cache.set(prefix, suggestions)
    metrics.record_latency((time.perf_counter() - started) * 1000)
    return {"query": q, "suggestions": suggestions, "source": "store"}


class SearchBody(BaseModel):
    query: str


@app.post("/search")
def search(body: SearchBody):
    """
    Dummy search endpoint. Returns "Searched" and records the query:
      1. bump trending (recent-activity) score
      2. update the in-memory trie immediately so suggestions reflect it
      3. buffer the durable count update for the next batch flush
      4. invalidate cached suggestion lists for the query's prefixes
    """
    query = _normalize(body.query)
    if not query:
        return JSONResponse(status_code=400, content={"message": "empty query"})

    trie: Trie = state["trie"]
    trending: TrendingTracker = state["trending"]
    cache: DistributedCache = state["cache"]
    batch_writer: BatchWriter = state["batch_writer"]

    trending.record(query)
    trie.increment(query, delta=1, initial=config.NEW_QUERY_INITIAL_COUNT)
    batch_writer.add(query)
    cache.invalidate_prefixes(query)

    return {"message": "Searched", "query": query}


@app.get("/cache/debug")
def cache_debug(prefix: str = Query("", description="prefix key to route")):
    """Show which cache node owns a prefix and whether it's currently a hit."""
    cache: DistributedCache = state["cache"]
    info = cache.debug(_normalize(prefix))
    info["nodes"] = cache.node_stats()
    return info


@app.get("/trending")
def trending_now():
    """Current trending queries, ranked by decayed recent activity."""
    trending: TrendingTracker = state["trending"]
    trie: Trie = state["trie"]
    items = trending.top(config.TRENDING_SIZE)
    return {
        "trending": [
            {"query": q, "recent_score": round(s, 3), "all_time_count": trie.count_of(q)}
            for q, s in items
        ]
    }


@app.get("/metrics")
def get_metrics():
    """Performance snapshot: latency p95, cache hit rate, DB read/write counts."""
    cache: DistributedCache = state["cache"]
    snap = metrics.snapshot()
    snap["cache"]["nodes"] = cache.node_stats()
    snap["ranking_mode"] = config.RANKING_MODE
    snap["trie_size"] = state["trie"].size()
    snap["pending_writes"] = state["batch_writer"].pending()
    return snap


# --- UI ----------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(f"{config.STATIC_DIR}/index.html")


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
