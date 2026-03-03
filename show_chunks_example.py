"""
Shows what law chunks look like before any embedding or storage.
Uses the vaccination KB from the test run (2025000841) — 20 articles, 2523 words.
"""

import sys, os, hashlib
sys.path.insert(0, os.path.dirname(__file__))

import requests
from bs4 import BeautifulSoup
from backend.scraper import split_into_articles, is_substantive

# The KB about vaccination against bluetongue/EHD (26 jan 2025)
DOC = {
    "ref_number":  "2025000841",
    "pub_date":    "31 januari 2025",
    "short_text":  "26 januari 2025. - Koninklijk besluit betreffende de vaccinatie tegen de epizoötische hemorragische ziekte en tegen blauwtong",
    "doc_type":    "Koninklijk besluit",
    "url": (
        "https://www.ejustice.just.fgov.be/cgi/article.pl"
        "?language=nl&sum_date=2025-01-31&pd_search=2025-01-31"
        "&numac_search=2025000841&page=1&lg_txt=N&caller=list"
        "&2025000841=0&view_numac=&dt=Koninklijk+besluit"
        "&pdd=2025-01-20&pdf=2025-01-31&choix1=en&choix2=en"
        "&fr=f&nl=n&du=d&trier=afkondiging"
    ),
}

# ── Fetch full text ──────────────────────────────────────────────────────────
print("Fetching document...", end=" ", flush=True)
page = requests.get(DOC["url"], timeout=15)
soup = BeautifulSoup(page.text, "lxml")
main = soup.find("main", {"class": "page__inner page__inner--content article-text"})
full_text = main.get_text(separator="\n", strip=True) if main else ""
print(f"done ({len(full_text.split())} words)\n")

# ── Split into articles ──────────────────────────────────────────────────────
articles = split_into_articles(full_text)

# ── Show what each chunk row would look like in law_chunks table ─────────────
print("=" * 72)
print("  WHAT LAW_CHUNKS TABLE ROWS LOOK LIKE")
print("=" * 72)
print(f"  Source : {DOC['ref_number']} — {DOC['short_text'][:60]}...")
print(f"  Type   : {DOC['doc_type']}")
print(f"  Total articles (chunks) : {len(articles)}")
print()

for i, art in enumerate(articles):
    # chunk_id = deterministic hash so re-runs are idempotent
    chunk_id = hashlib.md5(
        f"{DOC['ref_number']}|{art['article_num']}".encode()
    ).hexdigest()[:16]

    word_count = len(art["text"].split())
    preview    = art["text"][:200].replace("\n", " ")

    print(f"  ┌─ Row {i+1:>2} {'─'*55}")
    print(f"  │  chunk_id    : {chunk_id}")
    print(f"  │  numac       : {DOC['ref_number']}")
    print(f"  │  doc_type    : {DOC['doc_type']}")
    print(f"  │  pub_date    : {DOC['pub_date']}")
    print(f"  │  article_num : {art['article_num']}")
    print(f"  │  word_count  : {word_count}")
    print(f"  │  embedding   : vector(1536)  ← would be here after OpenAI call")
    print(f"  │  text preview:")
    print(f"  │    {preview}{'...' if len(art['text']) > 200 else ''}")
    print(f"  └{'─'*63}")
    print()

print("=" * 72)
print(f"  Total chunks from this one document : {len(articles)}")
print(f"  Each chunk → 1 embedding call → 1 row in law_chunks")
print("=" * 72)
