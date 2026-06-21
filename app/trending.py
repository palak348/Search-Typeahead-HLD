"""
Recency-aware ranking and trending.

The basic ranking sorts purely by all-time count. The enhanced ranking blends
that with *recent* activity so a query that's spiking right now can rise above a
historically-popular-but-quiet one.

How recent activity is tracked:
    For each searched query we keep a single number: a time-decayed score. Each
    search adds 1.0 to it, but the existing value is first decayed by how much
    time has passed. This is an exponential moving sum with a half-life — no need
    to store individual timestamps or fixed buckets.

        decay over elapsed t  =  exp(-lambda * t),  lambda = ln(2) / half_life

How it affects ranking:
    effective_score(q) = all_time_count(q) + RECENCY_BOOST * recent_score(q)
    RECENCY_BOOST converts "decayed recent searches" into "equivalent all-time
    counts" so the two live on the same scale and can be added.

Why short-lived spikes don't dominate forever:
    recent_score decays continuously toward 0. Once a query stops being searched,
    its boost melts away over a few half-lives and it falls back to its all-time
    rank. Nothing is permanently over-ranked.
"""
import math
import threading
import time

from .config import RECENCY_BOOST, RECENCY_HALF_LIFE_SECONDS, TRENDING_SIZE


class TrendingTracker:
    def __init__(self, half_life: float = RECENCY_HALF_LIFE_SECONDS,
                 boost: float = RECENCY_BOOST):
        self.boost = boost
        self._lambda = math.log(2) / half_life
        self._scores = {}      # query -> [decayed_score, last_update_ts]
        self._lock = threading.Lock()

    def _decay(self, score: float, last_ts: float, now: float) -> float:
        if score <= 0.0:
            return 0.0
        return score * math.exp(-self._lambda * (now - last_ts))

    def record(self, query: str, now: float = None):
        """Register one search for `query`."""
        now = now if now is not None else time.time()
        with self._lock:
            score, last_ts = self._scores.get(query, (0.0, now))
            self._scores[query] = [self._decay(score, last_ts, now) + 1.0, now]

    def recent_score(self, query: str, now: float = None) -> float:
        now = now if now is not None else time.time()
        with self._lock:
            entry = self._scores.get(query)
            if entry is None:
                return 0.0
            return self._decay(entry[0], entry[1], now)

    def boost_for(self, query: str, now: float = None) -> float:
        """Additive ranking boost for `query` on the all-time-count scale."""
        return self.boost * self.recent_score(query, now)

    def prefix_matches(self, prefix: str):
        """Recently-trending queries that start with `prefix` (for re-ranking)."""
        with self._lock:
            return [q for q in self._scores if q.startswith(prefix)]

    def top(self, k: int = TRENDING_SIZE, now: float = None):
        """Current trending queries, ranked purely by decayed recent score."""
        now = now if now is not None else time.time()
        with self._lock:
            scored = [
                (q, self._decay(s, t, now)) for q, (s, t) in self._scores.items()
            ]
        scored = [(q, s) for q, s in scored if s > 1e-3]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
