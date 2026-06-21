"""
Performance benchmark against a running server.

Reports:
  * /suggest latency including p95 (cold vs warm cache)
  * cache hit rate
  * database write reduction from batching
  * a basic-vs-recency ranking demonstration

Start the server first, then run:
    python -m bench.benchmark
    python -m bench.benchmark --host http://127.0.0.1:8000 --requests 3000
"""
import argparse
import json
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PREFIX_SEEDS = ["i", "ip", "iph", "ipa", "ja", "jav", "best", "be", "how",
                "goo", "ama", "net", "py", "re", "do", "da", "we", "cl", "tr", "ch"]


def _get(host, path):
    with urllib.request.urlopen(host + path, timeout=10) as r:
        return json.loads(r.read())


def _post(host, path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(host + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _percentile(values, pct):
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100 * len(s) + 0.5)) - 1))
    return s[k]


def latency_run(host, n, prefixes, label):
    lat = []
    for _ in range(n):
        p = random.choice(prefixes)
        t0 = time.perf_counter()
        _get(host, f"/suggest?q={p}")
        lat.append((time.perf_counter() - t0) * 1000)
    print(f"\n{label} ({n} requests):")
    print(f"  p50={_percentile(lat,50):.3f}ms  p95={_percentile(lat,95):.3f}ms  "
          f"p99={_percentile(lat,99):.3f}ms  avg={sum(lat)/len(lat):.3f}ms")
    return lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    ap.add_argument("--requests", type=int, default=2000)
    args = ap.parse_args()
    host = args.host.rstrip("/")

    random.seed(7)
    # Build a realistic prefix mix: a hot set repeated often + some variety.
    prefixes = PREFIX_SEEDS * 20

    print("=" * 64)
    print("SEARCH TYPEAHEAD - PERFORMANCE BENCHMARK")
    print("=" * 64)

    # 1) Cold then warm latency (cache effect).
    latency_run(host, len(set(prefixes)), list(set(prefixes)), "Cold cache (unique prefixes)")
    latency_run(host, args.requests, prefixes, "Warm cache (repeated prefixes)")

    # 2) Concurrency check.
    def worker():
        p = random.choice(prefixes)
        t0 = time.perf_counter()
        _get(host, f"/suggest?q={p}")
        return (time.perf_counter() - t0) * 1000
    with ThreadPoolExecutor(max_workers=20) as ex:
        conc = list(ex.map(lambda _: worker(), range(args.requests)))
    print(f"\nConcurrent (20 workers, {args.requests} requests):")
    print(f"  p50={_percentile(conc,50):.3f}ms  p95={_percentile(conc,95):.3f}ms  "
          f"p99={_percentile(conc,99):.3f}ms")

    # 3) Write reduction from batching.
    print("\n" + "-" * 64)
    before = _get(host, "/metrics")["database"]["writes"]
    N = 1000
    hot = ["benchmark hot query", "benchmark warm query", "benchmark cold query"]
    for i in range(N):
        _post(host, "/search", {"query": random.choice(hot)})
    time.sleep(6)  # allow a time-based flush
    after = _get(host, "/metrics")
    writes = after["database"]["writes"] - before
    print(f"Batch writes: submitted {N} searches across {len(hot)} distinct queries")
    print(f"  -> DB rows written for them: ~{writes} (aggregation + batching)")
    print(f"  -> overall write_reduction so far: "
          f"{after['batching']['write_reduction']*100:.1f}%")

    # 4) Cache hit rate snapshot.
    m = _get(host, "/metrics")
    print(f"\nCache hit rate: {m['cache']['hit_rate']*100:.1f}% "
          f"({m['cache']['hits']} hits / {m['cache']['misses']} misses)")
    print("Per-node cache sizes:")
    for node in m["cache"]["nodes"]:
        print(f"  {node['name']}: size={node['size']} hits={node['hits']} misses={node['misses']}")

    # 5) Basic vs recency ranking demonstration.
    print("\n" + "-" * 64)
    print("RANKING DEMO (basic popularity vs recency-aware)")
    prefix = "iphone"
    base = _get(host, f"/suggest?q={prefix}")["suggestions"]
    target = base[-1]["query"] if base else None
    print(f"Prefix '{prefix}' - current top 3 (recency mode reflects prior spikes):")
    for s in base[:3]:
        print(f"  score={s['score']:>12,.0f}  count={s['count']:>10,}  {s['query']}")
    if target:
        print(f"\nNow spiking the LOWEST-ranked one 80x: '{target}'")
        for _ in range(80):
            _post(host, "/search", {"query": target})
        after = _get(host, f"/suggest?q={prefix}")["suggestions"]
        pos = next((i for i, s in enumerate(after) if s["query"] == target), None)
        print(f"After spike, '{target}' is now at position "
              f"{pos+1 if pos is not None else 'N/A'} of {len(after)}:")
        for s in after[:3]:
            mark = "  <-- spiked" if s["query"] == target else ""
            print(f"  score={s['score']:>12,.0f}  count={s['count']:>10,}  {s['query']}{mark}")
    print("\nIn basic mode (RANKING_MODE=popularity) the order would NOT change,")
    print("because basic ranking ignores recent activity. Restart with")
    print("RANKING_MODE=popularity to see the contrast.")
    print("=" * 64)


if __name__ == "__main__":
    main()
