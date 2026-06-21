# Screenshots

Captures of the running system, referenced from the main [README](../README.md).

| File | Shows |
|------|-------|
| `01-ui-suggestions.png` | Live suggestion dropdown for the prefix `ip`, ranked by count. |
| `02-search-response.png` | A submitted search returning `{"message":"Searched"}`. |
| `03-trending.png` | The trending panel, ranked by decayed recent activity. |
| `04-ranking-before.png` | Suggestions for `iph` in normal popularity order. |
| `05-ranking-after-recency.png` | Same prefix after repeated searches — a low all-time query rises to the top via the recency boost. |
| `06-cache-debug.png` | `/cache/debug` — which node owns a prefix, ring position, HIT/MISS. |
| `07-metrics.png` | `/metrics` — latency percentiles, cache hit rate, write reduction. |
| `08-consistent-hashing.png` | Ring distribution and rebalancing vs naive modulo hashing. |
| `09-benchmark.png` | End-to-end benchmark: latency, hit rate, write reduction, ranking demo. |
