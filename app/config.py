"""
Central configuration for the typeahead system.

Every tunable lives here so the design is easy to explain and adjust at runtime.
Values can be overridden with environment variables where noted.
"""
import os

# --- Paths -------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "typeahead.db"))
DATASET_CSV = os.environ.get("DATASET_CSV", os.path.join(DATA_DIR, "queries.csv"))

# --- Suggestions -------------------------------------------------------------
MAX_SUGGESTIONS = 10          # results returned to the UI (at most 10)
CANDIDATE_POOL = 25           # top-N kept per trie node; gives recency re-ranking room

# --- Distributed cache -------------------------------------------------------
# Multiple *logical* cache nodes living in-process. Consistent hashing decides
# which node owns a given prefix key.
CACHE_NODES = ["cache-node-0", "cache-node-1", "cache-node-2"]
VIRTUAL_NODES = 150           # replicas per physical node on the ring (even spread)
CACHE_TTL_SECONDS = 30        # suggestion entries expire after this long
CACHE_MAX_ENTRIES = 5000      # per-node soft cap (LRU eviction beyond this)

# --- Batch writer ------------------------------------------------------------
# Search submissions are buffered and flushed to SQLite in batches instead of
# one write per request.
BATCH_SIZE = 200              # flush once this many submissions are buffered ...
BATCH_INTERVAL_SECONDS = 5.0  # ... or this many seconds pass, whichever first
NEW_QUERY_INITIAL_COUNT = 1   # count assigned to a never-seen query on first search

# --- Ranking / trending ------------------------------------------------------
# "popularity" -> sort purely by all-time count
# "recency"    -> blend all-time count with decayed recent activity
RANKING_MODE = os.environ.get("RANKING_MODE", "recency")
RECENCY_HALF_LIFE_SECONDS = 1800.0   # a query's recent-score halves every 30 min
RECENCY_BOOST = 5000.0               # 1 decayed recent search ~ this many all-time counts
TRENDING_SIZE = 10                   # entries returned by /trending
