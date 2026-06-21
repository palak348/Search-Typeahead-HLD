"""
Synthetic dataset generator (offline fallback).

Produces a CSV of `query,count` with 100k+ distinct, realistic-looking search
queries that deliberately share prefixes (great for demoing typeahead) and
follow a Zipf-like popularity distribution (a few very popular queries, a long
tail of rare ones) — the shape real search traffic has.

Usage:
    python -m scripts.generate_dataset                 # -> data/queries.csv
    python -m scripts.generate_dataset --rows 150000 --out data/queries.csv
"""
import argparse
import csv
import os
import random

from app import config

# Seed vocabulary. Bases share first letters so prefixes like "i", "ip" return
# rich suggestion lists. Modifiers tack on to create related multi-word queries.
BASES = [
    "iphone", "ipad", "imac", "instagram", "intel i7", "indeed jobs",
    "samsung galaxy", "samsung tv", "sony headphones", "spotify premium",
    "java tutorial", "javascript", "java spring boot", "python", "pytorch",
    "macbook pro", "macbook air", "machine learning", "marvel movies",
    "google maps", "gmail login", "github", "graphql", "golang",
    "amazon prime", "android studio", "apple watch", "airpods pro",
    "netflix", "nike shoes", "nvidia gpu", "notion app",
    "best laptop", "best smartphone", "best headphones", "best coffee maker",
    "cheap flights", "credit card", "chatgpt", "cloud computing",
    "react js", "redis", "rust language", "raspberry pi",
    "data structures", "docker compose", "django", "deep learning",
    "tesla model 3", "trending news", "travel insurance", "tax calculator",
    "weather today", "world cup", "wireless earbuds", "web development",
    "how to cook pasta", "how to invest", "how to learn coding",
    "online courses", "open source", "operating system",
]

MODIFIERS = [
    "", "review", "price", "2024", "2025", "near me", "online", "cheap",
    "best", "pro", "max", "case", "charger", "deals", "vs", "tutorial",
    "for beginners", "specs", "release date", "comparison", "discount",
    "free", "download", "alternatives", "setup", "guide", "tips", "ranking",
    "buy", "second hand", "refurbished", "warranty", "accessories",
    "screen size", "battery life", "camera", "color options", "in stock",
]

EXTRA = [
    "plus", "mini", "ultra", "lite", "x", "se", "premium", "basic",
    "student discount", "with case", "16gb", "32gb", "256gb", "512gb",
    "blue", "black", "white", "red", "green", "silver", "gold",
]

TAIL = [
    "", "amazon", "flipkart", "ebay", "best buy", "reddit", "youtube",
    "quora", "official site", "near me", "india", "usa", "uk", "2026",
]


def build_queries(target_rows: int):
    """
    Build distinct queries by layering suffix slots until we hit target_rows.
    Slots are added base -> modifier -> extra -> tail so the combinatorial space
    (tens of bases x modifiers x extras x tails) comfortably exceeds 100k while
    every query still shares a real prefix with its base.
    """
    seen = set()
    queries = []

    def add(*parts):
        q = " ".join(p for p in parts if p).strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
        return len(queries) >= target_rows

    # Depth 1: base + modifier
    for base in BASES:
        for mod in MODIFIERS:
            if add(base, mod):
                return queries
    # Depth 2: base + modifier + extra
    for base in BASES:
        for mod in MODIFIERS:
            for ex in EXTRA:
                if add(base, mod, ex):
                    return queries
    # Depth 3: base + modifier + extra + tail
    for base in BASES:
        for mod in MODIFIERS:
            for ex in EXTRA:
                for tl in TAIL:
                    if add(base, mod, ex, tl):
                        return queries
    return queries


def assign_counts(queries, seed: int = 42):
    """Zipf-like counts: popularity ~ C / rank^0.85, plus jitter, floored at 1."""
    rng = random.Random(seed)
    rng.shuffle(queries)                 # randomize which queries are popular
    n = len(queries)
    rows = []
    C = 2_000_000
    for rank, q in enumerate(queries, start=1):
        base = C / (rank ** 0.85)
        jitter = rng.uniform(0.7, 1.3)
        count = max(1, int(base * jitter))
        rows.append((q, count))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Generate a synthetic query dataset.")
    ap.add_argument("--rows", type=int, default=120_000,
                    help="number of distinct queries to generate (default 120000)")
    ap.add_argument("--out", default=config.DATASET_CSV,
                    help="output CSV path (default data/queries.csv)")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    queries = build_queries(args.rows)
    rows = assign_counts(queries)
    rows.sort(key=lambda r: r[1], reverse=True)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["query", "count"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} queries to {args.out}")
    print("Top 5 by count:")
    for q, c in rows[:5]:
        print(f"  {c:>10,}  {q}")


if __name__ == "__main__":
    main()
