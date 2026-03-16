"""
Law chunks store — pgvector table for Belgian regulatory articles.

Table: law_chunks
  Each row = one article from a scraped law (Wet, KB, Decreet, etc.)
  Embedding: OpenAI text-embedding-3-small (1536 dims)
  Idempotent: ON CONFLICT (chunk_id) DO UPDATE — safe to re-run
"""

import hashlib
import os
from typing import Optional

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

# ── Load credentials from RIA-Project .env ───────────────────────────────────
try:
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(__file__), "..", "..", "RIA-Project", ".env")
    if os.path.exists(_env):
        load_dotenv(_env)
    else:
        load_dotenv()
except ImportError:
    pass

_OPENAI_CLIENT = None


def _openai():
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        from openai import OpenAI
        _OPENAI_CLIENT = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _OPENAI_CLIENT


def _connect():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", 25060)),
        database=os.environ["POSTGRES_DATABASE"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        sslmode=os.environ.get("POSTGRES_SSLMODE", "require"),
    )


# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS law_chunks (
    id          SERIAL PRIMARY KEY,
    chunk_id    TEXT    UNIQUE NOT NULL,
    numac       TEXT    NOT NULL,
    doc_type    TEXT    NOT NULL,
    title       TEXT,
    pub_date    TEXT,
    article_num TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    word_count  INTEGER,
    url         TEXT,
    language    TEXT     DEFAULT 'nl',
    prediction  SMALLINT,          -- 0 = no burden, 1 = has burden, NULL = not predicted
    certainty   FLOAT,             -- model confidence: -1.0 to 1.0
    embedding   vector(1536),
    created_at  TIMESTAMPTZ DEFAULT now()
);
"""

# Run once against an existing DB to add the new columns
MIGRATE_ADD_PREDICTION_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='law_chunks' AND column_name='prediction') THEN
        ALTER TABLE law_chunks ADD COLUMN prediction SMALLINT;
        ALTER TABLE law_chunks ADD COLUMN certainty  FLOAT;
    END IF;
END$$;
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS law_chunks_embedding_idx ON law_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);",
    "CREATE INDEX IF NOT EXISTS law_chunks_numac_idx     ON law_chunks (numac);",
    "CREATE INDEX IF NOT EXISTS law_chunks_doctype_idx   ON law_chunks (doc_type);",
]


def create_table(conn=None):
    """Create law_chunks table and indexes if they don't exist."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(CREATE_TABLE_SQL)
            cur.execute(MIGRATE_ADD_PREDICTION_SQL)  # no-op if columns already exist
            for idx_sql in CREATE_INDEXES_SQL:
                try:
                    cur.execute(idx_sql)
                except Exception:
                    pass  # ivfflat needs data first; plain btree indexes will work
        conn.commit()
        print("✅ law_chunks table ready")
    finally:
        if own:
            conn.close()


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    """Embed a single text string using OpenAI text-embedding-3-small."""
    text = text[:8000]  # token safety
    resp = _openai().embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


def embed_batch(texts: list[str], batch_size: int = 100) -> list[list[float]]:
    """Embed a list of texts in batches."""
    results = []
    for i in range(0, len(texts), batch_size):
        batch = [t[:8000] for t in texts[i: i + batch_size]]
        resp = _openai().embeddings.create(
            model="text-embedding-3-small",
            input=batch,
        )
        results.extend([r.embedding for r in resp.data])
    return results


# ── Chunk ID ──────────────────────────────────────────────────────────────────

def make_chunk_id(numac: str, article_num: str) -> str:
    """Deterministic ID — safe to re-ingest same document without duplicates."""
    return hashlib.md5(f"{numac}|{article_num}".encode()).hexdigest()[:16]


# ── Store ─────────────────────────────────────────────────────────────────────

def store_chunks(items: list[dict], conn=None) -> int:
    """
    Embed and store sliding-window chunks from scraper output.

    Args:
        items:  List of scraper result dicts with embed=True.
                Each item must have: ref_number, doc_type, short_text,
                pub_date, url, articles (list of {article_num, text}).
                Optionally: chunk_predictions (dict of article_num ->
                {prediction: int, certainty: float}) set by main.py
                after running predict_documents() per chunk.
        conn:   Optional existing psycopg2 connection (for testing).

    Returns:
        Number of rows upserted.
    """
    own = conn is None
    if own:
        conn = _connect()

    # Build flat list of (chunk_id, text, metadata) for all chunks
    meta = []
    texts = []

    for item in items:
        if not item.get("embed") or not item.get("articles"):
            continue
        chunk_preds = item.get("chunk_predictions", {})
        for art in item["articles"]:
            if not art["text"].strip():
                continue
            chunk_id = make_chunk_id(item["ref_number"], art["article_num"])
            pred_info = chunk_preds.get(art["article_num"], {})
            texts.append(art["text"])
            meta.append({
                "chunk_id":    chunk_id,
                "numac":       item["ref_number"],
                "doc_type":    item["doc_type"],
                "title":       item.get("short_text", "")[:500],
                "pub_date":    item.get("pub_date", ""),
                "article_num": art["article_num"],
                "text":        art["text"],
                "word_count":  len(art["text"].split()),
                "url":         item.get("url", ""),
                "prediction":  pred_info.get("prediction"),   # None if not predicted
                "certainty":   pred_info.get("certainty"),
            })

    if not texts:
        return 0

    # Deduplicate by chunk_id
    seen: dict[str, int] = {}
    for i, m in enumerate(meta):
        seen[m["chunk_id"]] = i
    unique_indices = list(seen.values())
    meta  = [meta[i]  for i in unique_indices]
    texts = [texts[i] for i in unique_indices]

    print(f"  Embedding {len(texts)} chunks...", end=" ", flush=True)
    embeddings = embed_batch(texts)
    print("done")

    rows = [
        (
            m["chunk_id"], m["numac"], m["doc_type"], m["title"],
            m["pub_date"], m["article_num"], m["text"], m["word_count"],
            m["url"], "nl", m["prediction"], m["certainty"],
            embeddings[i],
        )
        for i, m in enumerate(meta)
    ]

    upsert_sql = """
        INSERT INTO law_chunks
            (chunk_id, numac, doc_type, title, pub_date,
             article_num, text, word_count, url, language,
             prediction, certainty, embedding)
        VALUES %s
        ON CONFLICT (chunk_id) DO UPDATE SET
            text        = EXCLUDED.text,
            word_count  = EXCLUDED.word_count,
            prediction  = EXCLUDED.prediction,
            certainty   = EXCLUDED.certainty,
            embedding   = EXCLUDED.embedding,
            title       = EXCLUDED.title,
            pub_date    = EXCLUDED.pub_date,
            url         = EXCLUDED.url
    """

    try:
        with conn.cursor() as cur:
            execute_values(
                cur, upsert_sql, rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
            )
        conn.commit()
    finally:
        if own:
            conn.close()

    return len(rows)


# ── Search ────────────────────────────────────────────────────────────────────

def search_law_chunks(
    query: str,
    k: int = 8,
    doc_type_filter: Optional[list[str]] = None,
    conn=None,
) -> list[dict]:
    """
    Semantic search over law_chunks.

    Args:
        query:           Natural language query or proposal text.
        k:               Number of results to return.
        doc_type_filter: Optional list of doc_types to restrict search
                         e.g. ["Wet", "Decreet"]
        conn:            Optional existing connection.

    Returns:
        List of dicts with keys: chunk_id, numac, doc_type, title,
        article_num, text, similarity, url
    """
    own = conn is None
    if own:
        conn = _connect()

    try:
        query_vec = embed_text(query)
        vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"

        filter_clause = ""
        params = [vec_str, vec_str, k]

        if doc_type_filter:
            placeholders = ",".join(["%s"] * len(doc_type_filter))
            filter_clause = f"WHERE doc_type IN ({placeholders})"
            params = [vec_str, vec_str] + list(doc_type_filter) + [k]

        sql = f"""
            SELECT
                chunk_id,
                numac,
                doc_type,
                title,
                pub_date,
                article_num,
                text,
                url,
                1 - (embedding <=> %s::vector) AS similarity
            FROM law_chunks
            {filter_clause}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """

        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        if own:
            conn.close()


# ── Direct title / numac lookup ───────────────────────────────────────────────

def search_law_chunks_by_title(
    terms: list[str],
    k: int = 20,
    conn=None,
) -> list[dict]:
    """
    Full-text ILIKE search against law_chunks.title and numac.

    Used when the proposal text contains an explicit law reference
    (e.g., "Koninklijk Besluit van 28 december 1944").  Each term in
    *terms* is ANDed together via ILIKE wildcards so that date fragments
    ("1944"), keyword phrases ("sociale zekerheid"), or partial NUMAC
    numbers can be combined.

    Args:
        terms: List of string fragments (case-insensitive, partial match).
               E.g. ["1944", "december", "sociale zekerheid"]
        k:     Max rows to return.
        conn:  Optional existing psycopg2 connection.

    Returns:
        List of dicts with keys: chunk_id, numac, doc_type, title,
        pub_date, article_num, text, url.  No similarity field.
    """
    if not terms:
        return []

    own = conn is None
    if own:
        conn = _connect()

    def _query(cur, search_terms):
        clauses, params = [], []
        for t in search_terms:
            clauses.append("(title ILIKE %s OR numac ILIKE %s)")
            params.extend([f"%{t}%", f"%{t}%"])
        params.append(k)
        sql = f"""
            SELECT chunk_id, numac, doc_type, title, pub_date,
                   article_num, text, url
            FROM   law_chunks
            WHERE  {" AND ".join(clauses)}
            ORDER  BY pub_date DESC NULLS LAST
            LIMIT  %s
        """
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    try:
        with conn.cursor() as cur:
            # Try with all terms; if empty, progressively drop the last term
            # (most specific → least specific fallback), minimum 2 terms
            for n in range(len(terms), 1, -1):
                rows = _query(cur, terms[:n])
                if rows:
                    return rows
            return []
    finally:
        if own:
            conn.close()


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats(conn=None) -> dict:
    """Return row counts by doc_type."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT doc_type, COUNT(*) AS chunks, COUNT(DISTINCT numac) AS documents
                FROM law_chunks
                GROUP BY doc_type
                ORDER BY chunks DESC
            """)
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM law_chunks")
            total = cur.fetchone()[0]
        return {
            "total_chunks": total,
            "by_type": [
                {"doc_type": r[0], "chunks": r[1], "documents": r[2]}
                for r in rows
            ],
        }
    finally:
        if own:
            conn.close()
