"""
Belgian Law Ingestion Pipeline
================================
Scraper → Filter → Embed → Store

Usage:
    # Ingest last 30 days, all substantive types
    python scripts/ingest_laws.py

    # Ingest specific date range
    python scripts/ingest_laws.py --start 2025-01-01 --end 2025-01-31

    # Ingest only Wetten and Decreten
    python scripts/ingest_laws.py --types Wet Decreet

    # Test run — 3 docs per type, no DB write
    python scripts/ingest_laws.py --dry-run --max 3

    # Show current DB stats
    python scripts/ingest_laws.py --stats
"""

import argparse
import sys
import os
from datetime import datetime, timedelta

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.scraper import (
    scrape_documents,
    ALWAYS_EMBED_TYPES,
    CONDITIONAL_TYPES,
    SKIP_TYPES,
)
from backend.law_store import create_table, store_chunks, get_stats

URL_SEARCH = "https://www.ejustice.just.fgov.be/cgi/rech.pl?language=nl"
URL_DETAIL = "https://www.ejustice.just.fgov.be"

# Default doc types to scrape (all substantive categories)
DEFAULT_DOC_TYPES = sorted(ALWAYS_EMBED_TYPES | CONDITIONAL_TYPES)


def progress(current, total):
    bar_len = 30
    filled = int(bar_len * current / total) if total else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  [{bar}] {current}/{total} fetched ", end="", flush=True)
    if current == total:
        print()


def print_results_summary(results: list[dict]):
    embed = [r for r in results if r.get("embed")]
    skip  = [r for r in results if not r.get("embed")]

    print(f"\n  {'─'*60}")
    print(f"  Scraped  : {len(results)} documents")
    print(f"  To embed : {len(embed)} documents")
    print(f"  Skipped  : {len(skip)} documents")

    total_chunks = sum(len(r.get("articles", [])) for r in embed)
    print(f"  Chunks   : {total_chunks} article-level chunks")

    if embed:
        print(f"\n  Documents to embed:")
        for r in embed:
            n_art = len(r.get("articles", []))
            print(f"    [{r['doc_type'][:20]:20s}] {r['ref_number']}  "
                  f"{n_art:>3} articles  {r['short_text'][:55]}")

    if skip:
        print(f"\n  Skipped (non-substantive):")
        for r in skip:
            wc = len(r.get("long_text", "").split())
            print(f"    [{r['doc_type'][:20]:20s}] {r['ref_number']}  "
                  f"{wc:>5} words  {r['short_text'][:50]}")


def main():
    parser = argparse.ArgumentParser(description="Ingest Belgian laws into law_chunks table")
    parser.add_argument("--start",  default=None,
                        help="Start date YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--end",    default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--types",  nargs="+", default=DEFAULT_DOC_TYPES,
                        help="Document types to scrape (space-separated)")
    parser.add_argument("--max",    type=int, default=None,
                        help="Max results per doc_type (useful for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scrape and classify but do NOT write to DB")
    parser.add_argument("--stats",  action="store_true",
                        help="Show current DB stats and exit")
    args = parser.parse_args()

    # ── Stats mode ────────────────────────────────────────────────────────────
    if args.stats:
        print("\n  law_chunks DB stats")
        print("  " + "─" * 50)
        stats = get_stats()
        print(f"  Total chunks : {stats['total_chunks']}")
        for row in stats["by_type"]:
            print(f"    {row['doc_type']:<30s}  {row['chunks']:>5} chunks  "
                  f"({row['documents']} documents)")
        print()
        return

    # ── Date range ────────────────────────────────────────────────────────────
    end_date   = datetime.strptime(args.end,   "%Y-%m-%d") if args.end   else datetime.now()
    start_date = datetime.strptime(args.start, "%Y-%m-%d") if args.start else end_date - timedelta(days=30)

    # Filter out explicit skip types
    doc_types = [t for t in args.types if t not in SKIP_TYPES]

    print(f"\n{'='*65}")
    print(f"  BELGIAN LAW INGESTION PIPELINE")
    print(f"{'='*65}")
    print(f"  Date range : {start_date.date()} → {end_date.date()}")
    print(f"  Doc types  : {', '.join(doc_types)}")
    print(f"  Max/type   : {args.max or 'unlimited'}")
    print(f"  Mode       : {'DRY RUN (no DB writes)' if args.dry_run else 'LIVE'}")
    print(f"{'='*65}\n")

    # ── Ensure table exists ───────────────────────────────────────────────────
    if not args.dry_run:
        create_table()

    # ── Scrape ────────────────────────────────────────────────────────────────
    print("  Scraping ejustice.just.fgov.be...")
    results = scrape_documents(
        start_date=start_date,
        end_date=end_date,
        doc_types=doc_types,
        url_searchpage=URL_SEARCH,
        url_detail_page=URL_DETAIL,
        progress_callback=progress,
        max_results=args.max,
    )

    print_results_summary(results)

    if args.dry_run:
        print("\n  [DRY RUN] No data written to DB.\n")
        return

    # ── Embed + Store ─────────────────────────────────────────────────────────
    to_embed = [r for r in results if r.get("embed") and r.get("articles")]
    if not to_embed:
        print("\n  Nothing to embed — done.\n")
        return

    print(f"\n  Storing {len(to_embed)} documents into law_chunks...")
    stored = store_chunks(to_embed)
    print(f"  ✅ {stored} chunks stored")

    # ── Final stats ───────────────────────────────────────────────────────────
    stats = get_stats()
    print(f"\n  DB total : {stats['total_chunks']} chunks across all laws")
    print()


if __name__ == "__main__":
    main()
