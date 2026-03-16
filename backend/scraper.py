import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Doc-type classification
# ---------------------------------------------------------------------------

# Always substantive — embed without further checks
ALWAYS_EMBED_TYPES = {
    "Wet", "Decreet", "Ordonnantie", "Programmawet",
    "Financiewet", "Samenwerkingsakkoord", "Verdrag", "Verordening",
}

# Fetch full text and apply is_substantive() before embedding
CONDITIONAL_TYPES = {
    "Koninklijk besluit", "Ministerieel besluit",
    "Omzendbrief", "Circulaire",
    "Beschikking", "Besluit", "Beroepsverenigingen",
}

# Skip entirely — individual admin acts, notices
SKIP_TYPES = {"Bericht", "Reglement"}

# Signals that identify appointment / admin acts (checked in first 600 chars)
_APPOINTMENT_SIGNALS = [
    "worden benoemd", "wordt benoemd", "zijn benoemd", "is benoemd",
    "sont nommés", "est nommé", "sont désignés",
    "wonende te", "demeurant à", "residing in",
    "wordt aangesteld", "zijn aangesteld",
]

# Regulatory-body names — their beschikkingen/besluiten ARE substantive
_REGULATORY_BODIES = [
    "fsma", "nbB", "bipt", "creg", "fanc", "favv",
    "mededingingsautoriteit", "autorité de la concurrence",
]


def is_substantive(text: str, doc_type: str) -> bool:
    """Return True if the document contains general regulatory rules worth embedding."""
    if not text:
        return False

    # Always-embed types: only skip if completely empty
    if doc_type in ALWAYS_EMBED_TYPES:
        return len(text.split()) >= 100

    # Conditional types: apply stricter checks
    if len(text.split()) < 300:
        return False

    text_lower = text.lower()
    first_600 = text_lower[:600]

    # Appointment / individual admin act — skip
    if any(sig in first_600 for sig in _APPOINTMENT_SIGNALS):
        # Exception: regulatory bodies can issue substantive beschikkingen
        if not any(body in text_lower[:200] for body in _REGULATORY_BODIES):
            return False

    # Count articles — use broader pattern to handle "Art. 1." and "Artikel 1."
    article_count = len(re.findall(
        r"Art(?:ikel|icle)?[.\s]\s*\d+", text
    ))
    if article_count < 3:
        return False

    return True


# ---------------------------------------------------------------------------
# Sliding-window chunking over full law text
# ---------------------------------------------------------------------------

_CHUNK_WORDS   = 400   # target chunk size in words
_OVERLAP_WORDS = 100   # overlap between consecutive chunks


def sliding_window_chunks(text: str, chunk_words: int = _CHUNK_WORDS,
                          overlap_words: int = _OVERLAP_WORDS) -> list:
    """Split full law text into overlapping fixed-size word windows.

    Each chunk is large enough to carry meaningful context for the
    administrative-burden classifier, and the overlap ensures that
    clause boundaries never fall in a dead zone between chunks.

    Returns a list of dicts: {article_num, text}
    where article_num is "chunk_N" (1-based).
    """
    words = text.split()
    if not words:
        return []

    step   = max(1, chunk_words - overlap_words)
    chunks = []
    i      = 0
    n      = 0

    while i < len(words):
        window = words[i: i + chunk_words]
        chunk_text = " ".join(window).strip()
        if chunk_text:
            n += 1
            chunks.append({"article_num": f"chunk_{n}", "text": chunk_text})
        i += step

    return chunks


# ---------------------------------------------------------------------------
# HTTP helpers (replaces Selenium — ejustice is a plain CGI site)
# ---------------------------------------------------------------------------

BASE_URL    = "https://www.ejustice.just.fgov.be/cgi"
SEARCH_URL  = BASE_URL + "/rech_res.pl"
LIST_URL    = BASE_URL + "/list.pl"
HEADERS     = {"Content-Type": "application/x-www-form-urlencoded"}
TODAY       = datetime.today().strftime("%Y-%m-%d")


def _fetch_search_page(url_searchpage: str) -> set:
    """Return the set of valid doc-type option texts from the search form."""
    resp = requests.get(url_searchpage, timeout=15)
    resp.encoding = "iso-8859-1"
    soup = BeautifulSoup(resp.text, "html.parser")
    select = soup.find("select", {"name": "dt"})
    if not select:
        return set()
    return {opt.text.strip() for opt in select.find_all("option") if opt.text.strip()}


def _parse_result_items(soup: BeautifulSoup, doc_type: str) -> list:
    """Extract result items from a parsed results/list page."""
    items = []
    for tag in soup.find_all("div", {"class": "list"}):
        contents = tag.find_all("div", {"class": "list-item--content"})
        buttons  = tag.find_all("div", {"class": "list-item--button"})
        for content, button in zip(contents, buttons):
            anchor   = content.find("a", href=True)
            pub_date = content.find("p", {"class": "list-item--date"})
            if not anchor:
                continue
            # href is relative to /cgi/ (e.g. "article.pl?...")
            # lg_txt=N serves the text version; lg_txt=Y serves the PDF/image — keep N
            href = anchor["href"]
            url  = urljoin(BASE_URL + "/", href)
            items.append({
                "ref_number": button.text.strip(),
                "pub_date":   pub_date.text if pub_date else "",
                "short_text": anchor.text.strip(),
                "url":        url,
                "doc_type":   doc_type,
            })
    return items


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

def scrape_documents(start_date: datetime, end_date: datetime, doc_types: list,
                     url_searchpage: str, url_detail_page: str,
                     progress_callback=None, max_results: int = None):
    """Scrape ejustice.just.fgov.be for Belgian regulatory documents.

    Uses plain HTTP requests — no browser or ChromeDriver required.

    Args:
        start_date:        Start of publication date range.
        end_date:          End of publication date range.
        doc_types:         List of document type strings matching the site dropdown.
        url_searchpage:    URL of the ejustice search form (used to verify types).
        url_detail_page:   Base URL for building detail page links.
        progress_callback: Optional callable(current, total) for progress reporting.
        max_results:       Cap total results per doc_type (useful for testing).

    Returns:
        List of dicts with keys:
          ref_number, pub_date, short_text, url, doc_type,
          long_text, articles, embed (bool)
    """
    valid_types = _fetch_search_page(url_searchpage)
    scraping_result = []

    for doc_type in doc_types:
        if doc_type in SKIP_TYPES:
            continue

        if valid_types and doc_type not in valid_types:
            print(f"  [skip] '{doc_type}' not found in site dropdown")
            continue

        type_results = []

        # First page: POST to the search endpoint
        resp = requests.post(
            SEARCH_URL,
            data={
                "dt":       doc_type,
                "pdd":      start_date.strftime("%Y-%m-%d"),
                "pdf":      end_date.strftime("%Y-%m-%d"),
                "language": "nl",
                "sum_date": TODAY,
            },
            headers=HEADERS,
            timeout=30,
        )
        resp.encoding = "iso-8859-1"

        while True:
            soup = BeautifulSoup(resp.text, "html.parser")
            page_items = _parse_result_items(soup, doc_type)
            for item in page_items:
                type_results.append(item)
                if max_results and len(type_results) >= max_results:
                    break

            if max_results and len(type_results) >= max_results:
                break

            # Follow pagination via GET to list.pl
            next_btn = soup.find("a", {"class": "pagination-button pagination-next"})
            if not next_btn:
                break

            next_url = urljoin(BASE_URL + "/", next_btn["href"])
            resp = requests.get(next_url, timeout=30)
            resp.encoding = "iso-8859-1"

        scraping_result.extend(type_results)

    # Phrases that indicate the page returned an error instead of law text
    _UNAVAILABLE_SIGNALS = [
        "niet beschikbaar in deze taal",
        "pas disponible dans cette langue",
        "not available in this language",
        "beeld van het belgisch staatsblad",
    ]

    def _extract_full_text(url: str) -> str:
        """
        Fetch the detail page and return clean law text.
        If Dutch text is unavailable, retry with French.
        Returns empty string if neither language has text.
        """
        for lang in ("nl", "fr"):
            lang_url = url.replace("language=nl", f"language={lang}") if lang == "fr" else url
            page = requests.get(lang_url, timeout=15)
            page.encoding = "iso-8859-1"
            soup = BeautifulSoup(page.text, "html.parser")
            # Try specific class first (original selector), fall back to article-text
            main = (
                soup.find("main", {"class": "page__inner page__inner--content article-text"})
                or soup.find("main", class_="article-text")
            )
            if not main:
                continue
            # Collect all paragraph text (avoids warning banners at top of page)
            paragraphs = [p.get_text(separator=" ", strip=True) for p in main.find_all("p")]
            text = "\n".join(p for p in paragraphs if p)
            text_lower = text.lower()
            if text and not any(sig in text_lower for sig in _UNAVAILABLE_SIGNALS):
                return text
        return ""

    # Fetch full text and classify each result
    total = len(scraping_result)
    for i, item in enumerate(scraping_result):
        try:
            full_text = _extract_full_text(item["url"])
            item["long_text"] = full_text

            if is_substantive(full_text, item["doc_type"]):
                item["articles"] = [{"article_num": "full", "text": full_text}]
                item["embed"] = True
            else:
                item["articles"] = []
                item["embed"] = False

        except Exception:
            item["long_text"] = ""
            item["articles"] = []
            item["embed"] = False

        if progress_callback:
            progress_callback(i + 1, total)

    return scraping_result
