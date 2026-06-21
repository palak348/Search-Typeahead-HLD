"""
Real open-source dataset loader: Wikimedia Pageviews.

Source (public domain, no login):
    https://dumps.wikimedia.org/other/pageviews/
    e.g. https://dumps.wikimedia.org/other/pageviews/2024/2024-01/pageviews-20240101-000000.gz

Each line of a pageviews file is:
    <domain_code> <page_title> <view_count> <bytes>
e.g.  "en iPhone 8421 0"

This already matches the required `query,count` shape: the page title acts as
the query and the view count is its popularity — no aggregation needed (though
we do merge case/underscore variants of the same title). We keep only English
Wikipedia article titles and drop administrative pages.

Usage:
    # download then convert (needs internet):
    python -m scripts.load_dataset --url <pageviews-url>

    # convert an already-downloaded .gz:
    python -m scripts.load_dataset --file path/to/pageviews-*.gz

    # both produce data/queries.csv (query,count), top --limit rows.
"""
import argparse
import csv
import gzip
import os
import urllib.request
from collections import defaultdict

from app import config

KEEP_DOMAINS = {"en", "en.m"}        # English Wikipedia (desktop + mobile)
SKIP_PREFIXES = (
    "Special:", "File:", "Template:", "Category:", "Help:", "Wikipedia:",
    "Portal:", "Talk:", "User:", "Draft:", "MediaWiki:", "Module:",
)


def _download(url: str, dest: str):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    print(f"Downloading {url} ...")
    urllib.request.urlretrieve(url, dest)
    print(f"Saved to {dest}")
    return dest


def _is_article(title: str) -> bool:
    if not title or title == "Main_Page":
        return False
    if title.startswith(SKIP_PREFIXES):
        return False
    return True


def _parse(path: str):
    counts = defaultdict(int)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.split(" ")
            if len(parts) < 3:
                continue
            domain, title, views = parts[0], parts[1], parts[2]
            if domain not in KEEP_DOMAINS or not _is_article(title):
                continue
            try:
                v = int(views)
            except ValueError:
                continue
            query = title.replace("_", " ").strip()
            if query:
                counts[query] += v
    return counts


def main():
    ap = argparse.ArgumentParser(description="Load Wikimedia Pageviews into queries.csv")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="pageviews .gz URL to download and convert")
    g.add_argument("--file", help="path to an already-downloaded pageviews .gz/.txt")
    ap.add_argument("--out", default=config.DATASET_CSV, help="output CSV path")
    ap.add_argument("--limit", type=int, default=200_000,
                    help="keep the top N titles by views (default 200000)")
    args = ap.parse_args()

    path = args.file
    if args.url:
        path = _download(args.url, os.path.join(config.DATA_DIR, "pageviews.gz"))

    print(f"Parsing {path} ...")
    counts = _parse(path)
    rows = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:args.limit]
    if len(rows) < 100_000:
        print(f"WARNING: only {len(rows)} rows (<100k). Use a busier hour or "
              f"concatenate multiple pageviews files for a larger dataset.")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "count"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} queries to {args.out}")
    for q, c in rows[:5]:
        print(f"  {c:>10,}  {q}")


if __name__ == "__main__":
    main()
