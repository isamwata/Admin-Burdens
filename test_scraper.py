"""
Quick test of the corrected scraper.
Fetches 3 results per doc_type for a narrow date window.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime
from backend.scraper import scrape_documents

URL_SEARCH  = "https://www.ejustice.just.fgov.be/cgi/rech.pl?language=nl"
URL_DETAIL  = "https://www.ejustice.just.fgov.be"

# Narrow date window — enough to find a few of each type
START = datetime(2025, 1, 20)
END   = datetime(2025, 1, 31)

# Test with 3 representative types: always-embed, conditional, previously-skipped
DOC_TYPES = [
    "Wet",                 # always substantive
    "Koninklijk besluit",  # conditional — mix of regulatory + appointment
    "Beroepsverenigingen", # previously skipped — now conditional
]

MAX_PER_TYPE = 3  # keep test fast


def progress(current, total):
    print(f"  fetching {current}/{total}...", end="\r")


print(f"\n{'='*70}")
print("  SCRAPER TEST")
print(f"  Date range : {START.date()} → {END.date()}")
print(f"  Doc types  : {', '.join(DOC_TYPES)}")
print(f"  Max/type   : {MAX_PER_TYPE}")
print(f"{'='*70}\n")

results = scrape_documents(
    start_date=START,
    end_date=END,
    doc_types=DOC_TYPES,
    url_searchpage=URL_SEARCH,
    url_detail_page=URL_DETAIL,
    progress_callback=progress,
    max_results=MAX_PER_TYPE,
)

print(f"\n\nTotal fetched: {len(results)}\n")

for item in results:
    embed_flag = "✅ EMBED" if item["embed"] else "❌ SKIP"
    article_count = len(item["articles"])
    word_count = len(item["long_text"].split())

    print(f"{'─'*70}")
    print(f"  [{item['doc_type']}]  {embed_flag}")
    print(f"  Ref     : {item['ref_number']}")
    print(f"  Date    : {item['pub_date']}")
    print(f"  Title   : {item['short_text'][:80]}")
    print(f"  Words   : {word_count}   Articles found: {article_count}")
    print(f"  URL     : {item['url']}")

    if item["articles"]:
        print(f"\n  First article preview:")
        first = item["articles"][0]
        preview = first["text"][:300].replace("\n", " ")
        print(f"    Art.{first['article_num']}: {preview}...")

    if not item["embed"] and item["long_text"]:
        print(f"\n  Skipped because: ", end="")
        wc = word_count
        if wc < 300:
            print(f"too short ({wc} words)")
        else:
            print("appointment/admin signals detected in first 600 chars")
            print(f"    → First 300 chars: {item['long_text'][:300].replace(chr(10), ' ')}")

print(f"\n{'='*70}")
summary = {"embed": 0, "skip": 0}
for item in results:
    summary["embed" if item["embed"] else "skip"] += 1
print(f"  Summary: {summary['embed']} to embed  |  {summary['skip']} skipped")
print(f"{'='*70}\n")
