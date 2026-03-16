"""
Batch historical ingestion — Belgian Staatsblad (June 1997 → present)

Processes one calendar month at a time:
  1. Scrape ejustice.just.fgov.be for each doc_type
  2. Run per-chunk ML prediction (administrative burden classifier)
  3. Embed chunks (OpenAI text-embedding-3-small)
  4. Upsert into law_chunks table with prediction labels

Tracks progress in scrape_log table — fully resumable.

Usage:
  python -m scripts.batch_ingest                      # full backfill
  python -m scripts.batch_ingest --start 1997-06      # from specific month
  python -m scripts.batch_ingest --start 2024-01 --end 2024-12
  python -m scripts.batch_ingest --month 2025-03      # single month
  python -m scripts.batch_ingest --dry-run            # list pending months

Environment variables required:
  POSTGRES_HOST, POSTGRES_DATABASE, POSTGRES_USER, POSTGRES_PASSWORD
  OPENAI_API_KEY
  POSTGRES_PORT (default 25060), POSTGRES_SSLMODE (default require)
"""

import argparse
import json
import os
import sys
from calendar import monthrange
from datetime import date, datetime

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# ── Load .env if present ─────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(__file__), "..", "..", "RIA-Project", ".env")
    if os.path.exists(_env):
        load_dotenv(_env)
    else:
        load_dotenv()
except ImportError:
    pass

# ── Project imports ───────────────────────────────────────────────────────────
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

from backend.scraper import scrape_documents
from backend.predictor import predict_documents
from backend.law_store import create_table, store_chunks, get_stats

# ── Config ────────────────────────────────────────────────────────────────────
with open(os.path.join(_root, "config.json")) as f:
    CONFIG = json.load(f)

# Only substantive doc types — skip Bericht, Reglement (handled by scraper too)
BATCH_DOC_TYPES = [
    "Koninklijk besluit",
    "Ministerieel besluit",
    "Wet",
    "Decreet",
    "Ordonnantie",
    "Besluit",
    "Programmawet",
    "Financiewet",
    "Samenwerkingsakkoord",
    "Verdrag",
    "Omzendbrief",
]

BACKFILL_START = date(1997, 6, 1)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _connect():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 25060)),
        database=os.environ["POSTGRES_DATABASE"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        sslmode=os.environ.get("POSTGRES_SSLMODE", "require"),
    )


def ensure_scrape_log(conn):
    """Create scrape_log table if not present."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_log (
                id          SERIAL PRIMARY KEY,
                month       DATE NOT NULL,          -- first day of the month
                doc_type    TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                chunks_in   INTEGER DEFAULT 0,      -- scraped docs
                chunks_stored INTEGER DEFAULT 0,    -- chunks written to DB
                error       TEXT,
                started_at  TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                UNIQUE(month, doc_type)
            )
        """)
    conn.commit()


def get_pending_months(conn, start: date, end: date, doc_types: list[str]) -> list[tuple]:
    """
    Return (month_date, doc_type) pairs that still need processing.
    Skips rows already marked 'done' in scrape_log.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT month, doc_type FROM scrape_log
            WHERE status = 'done'
              AND month  >= %s AND month <= %s
              AND doc_type = ANY(%s)
        """, (start, end, doc_types))
        done = {(r[0], r[1]) for r in cur.fetchall()}

    pending = []
    cur_month = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)
    while cur_month <= end_month:
        for dt in doc_types:
            if (cur_month, dt) not in done:
                pending.append((cur_month, dt))
        # advance to next month
        if cur_month.month == 12:
            cur_month = date(cur_month.year + 1, 1, 1)
        else:
            cur_month = date(cur_month.year, cur_month.month + 1, 1)
    return pending


def mark_log(conn, month: date, doc_type: str, status: str,
             chunks_in=0, chunks_stored=0, error=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO scrape_log (month, doc_type, status, chunks_in, chunks_stored,
                                    error, started_at, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s,
                    CASE WHEN %s='running' THEN now() ELSE NULL END,
                    CASE WHEN %s IN ('done','error') THEN now() ELSE NULL END)
            ON CONFLICT (month, doc_type) DO UPDATE SET
                status       = EXCLUDED.status,
                chunks_in    = EXCLUDED.chunks_in,
                chunks_stored= EXCLUDED.chunks_stored,
                error        = EXCLUDED.error,
                started_at   = CASE WHEN EXCLUDED.status='running'
                                    THEN now() ELSE scrape_log.started_at END,
                finished_at  = CASE WHEN EXCLUDED.status IN ('done','error')
                                    THEN now() ELSE scrape_log.finished_at END
        """, (month, doc_type, status, chunks_in, chunks_stored, error, status, status))
    conn.commit()


# ── Per-month processing ──────────────────────────────────────────────────────

def process_month(month: date, doc_type: str, conn, dry_run=False) -> dict:
    """
    Scrape + predict + embed + store one (month, doc_type) combination.
    Returns summary dict.
    """
    last_day = monthrange(month.year, month.month)[1]
    start_dt = datetime(month.year, month.month, 1)
    end_dt   = datetime(month.year, month.month, last_day)

    label = f"{month.strftime('%Y-%m')} / {doc_type}"
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")

    if dry_run:
        return {"month": month, "doc_type": doc_type, "status": "dry_run"}

    mark_log(conn, month, doc_type, "running")

    try:
        # 1. Scrape
        results = scrape_documents(
            start_date=start_dt,
            end_date=end_dt,
            doc_types=[doc_type],
            url_searchpage=CONFIG["scraping"]["url_searchpage"],
            url_detail_page=CONFIG["scraping"]["url_detail_page"],
        )
        to_embed = [r for r in results if r.get("embed") and r.get("articles")]
        total_chunks = sum(len(r["articles"]) for r in to_embed)
        print(f"  scraped {len(results)} docs → {len(to_embed)} substantive → {total_chunks} chunks")

        if not to_embed:
            mark_log(conn, month, doc_type, "done", chunks_in=len(results), chunks_stored=0)
            return {"month": month, "doc_type": doc_type, "status": "done", "stored": 0}

        # 2. Predict on full document text (one prediction per document)
        doc_rows = pd.DataFrame([
            {"long_text": item["long_text"], "ref_number": item["ref_number"],
             "doc_type": item["doc_type"], "short_text": item.get("short_text", "")}
            for item in to_embed
        ])
        pred_df = predict_documents(doc_rows, CONFIG["predictions"])

        for i, item in enumerate(to_embed):
            doc_prediction = int(pred_df.iloc[i]["prediction"])
            doc_certainty  = float(pred_df.iloc[i]["certainty"])
            # Stamp document-level prediction onto every chunk
            item["chunk_predictions"] = {
                art["article_num"]: {
                    "prediction": doc_prediction,
                    "certainty":  doc_certainty,
                }
                for art in item["articles"]
            }

        # 3. Embed + store
        stored = store_chunks(to_embed, conn=conn)
        print(f"  ✅ stored {stored} chunks in DB")

        mark_log(conn, month, doc_type, "done",
                 chunks_in=len(results), chunks_stored=stored)
        return {"month": month, "doc_type": doc_type, "status": "done", "stored": stored}

    except Exception as exc:
        msg = str(exc)
        print(f"  ❌ ERROR: {msg}")
        mark_log(conn, month, doc_type, "error", error=msg[:500])
        return {"month": month, "doc_type": doc_type, "status": "error", "error": msg}


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Batch ingest Belgian Staatsblad into law_chunks")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--month",  help="Process a single month, e.g. 2025-03")
    group.add_argument("--start",  help="Start month (YYYY-MM), default 1997-06")
    parser.add_argument("--end",   help="End month (YYYY-MM), default current month")
    parser.add_argument("--doc-types", nargs="+", default=BATCH_DOC_TYPES,
                        metavar="TYPE",
                        help="Document types to ingest (default: all substantive types)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List pending months without processing")
    parser.add_argument("--stats", action="store_true",
                        help="Print DB stats and exit")
    return parser.parse_args()


def _parse_month(s: str) -> date:
    """Parse 'YYYY-MM' into a date(YYYY, MM, 1)."""
    parts = s.split("-")
    return date(int(parts[0]), int(parts[1]), 1)


def main():
    args = parse_args()

    conn = _connect()

    if args.stats:
        stats = get_stats(conn=conn)
        print(f"\nTotal chunks in DB: {stats['total_chunks']}")
        for row in stats["by_type"]:
            print(f"  {row['doc_type']:35s} {row['chunks']:>7} chunks  {row['documents']:>5} docs")
        conn.close()
        return

    ensure_scrape_log(conn)
    create_table(conn=conn)

    today = date.today()

    if args.month:
        start = _parse_month(args.month)
        end   = start
    else:
        start = _parse_month(args.start) if args.start else BACKFILL_START
        end   = _parse_month(args.end)   if args.end   else date(today.year, today.month, 1)

    doc_types = args.doc_types

    pending = get_pending_months(conn, start, end, doc_types)
    total = len(pending)
    print(f"\nPending: {total} (month, doc_type) pairs to process")

    if args.dry_run:
        months_shown = set()
        for month, dt in pending[:50]:
            if month not in months_shown:
                print(f"  {month.strftime('%Y-%m')}")
                months_shown.add(month)
        if total > 50:
            print(f"  ... and {total - 50} more")
        conn.close()
        return

    done = 0
    errors = 0
    total_stored = 0
    for i, (month, doc_type) in enumerate(pending, 1):
        print(f"\n[{i}/{total}]", end="")
        result = process_month(month, doc_type, conn)
        if result["status"] == "done":
            done += 1
            total_stored += result.get("stored", 0)
        elif result["status"] == "error":
            errors += 1

    conn.close()

    print(f"\n{'='*60}")
    print(f"Batch complete: {done} succeeded, {errors} errors")
    print(f"Total chunks stored this run: {total_stored}")


if __name__ == "__main__":
    main()
