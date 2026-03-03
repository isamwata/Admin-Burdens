import os
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


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
        r"(?m)^\s*Art(?:ikel|icle)?[.\s]\s*\d+", text
    ))
    if article_count < 3:
        return False

    return True


# ---------------------------------------------------------------------------
# Article-level chunking
# ---------------------------------------------------------------------------

_ARTICLE_RE = re.compile(
    r"(?m)^\s*(Art(?:ikel|icle)?[.\s]\s*(\d+)[^\n]*)",
)


def split_into_articles(text: str) -> list:
    """Split full law text into article-level chunks.

    Returns a list of dicts: {article_num, text}
    Falls back to a single chunk when no article structure is found.
    """
    matches = list(_ARTICLE_RE.finditer(text))
    if not matches:
        return [{"article_num": "full", "text": text.strip()}]

    articles = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        article_text = text[start:end].strip()
        if article_text:
            articles.append({
                "article_num": match.group(2),
                "text": article_text,
            })
    return articles


# ---------------------------------------------------------------------------
# Chrome driver
# ---------------------------------------------------------------------------

def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--no-zygote")               # prevents zygote process crash in restricted containers
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--user-data-dir=/tmp/chrome-user-data")

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
    if chromedriver_path:
        from selenium.webdriver.chrome.service import Service
        return webdriver.Chrome(service=Service(chromedriver_path), options=options)

    return webdriver.Chrome(options=options)


# ---------------------------------------------------------------------------
# Main scraper
# ---------------------------------------------------------------------------

def scrape_documents(start_date: datetime, end_date: datetime, doc_types: list,
                     url_searchpage: str, url_detail_page: str,
                     progress_callback=None, max_results: int = None):
    """Scrape ejustice.just.fgov.be for Belgian regulatory documents.

    Args:
        start_date:       Start of publication date range.
        end_date:         End of publication date range.
        doc_types:        List of document type strings matching the site dropdown.
        url_searchpage:   URL of the ejustice search form.
        url_detail_page:  Base URL for building detail page links.
        progress_callback: Optional callable(current, total) for progress reporting.
        max_results:      Cap total results per doc_type (useful for testing).

    Returns:
        List of dicts with keys:
          ref_number, pub_date, short_text, url, doc_type,
          long_text, articles, embed (bool)
    """
    driver = get_driver()
    scraping_result = []

    try:
        for doc_type in doc_types:
            if doc_type in SKIP_TYPES:
                continue

            # Verify document type exists on the site
            driver.get(url_searchpage)
            element = driver.find_element(By.XPATH, "//select[@name='dt']")
            all_options = [opt.text for opt in element.find_elements(By.TAG_NAME, "option")]

            if doc_type not in all_options:
                print(f"  [skip] '{doc_type}' not found in site dropdown")
                continue

            # Fill and submit search form
            driver.get(url_searchpage)
            Select(driver.find_element(By.XPATH, "//select[@name='dt']")).select_by_value(doc_type)

            start_el = driver.find_element(By.XPATH, "//input[@name='pdd']")
            driver.execute_script("arguments[0].value = arguments[1]", start_el, start_date.strftime("%Y-%m-%d"))

            end_el = driver.find_element(By.XPATH, "//input[@name='pdf']")
            driver.execute_script("arguments[0].value = arguments[1]", end_el, end_date.strftime("%Y-%m-%d"))

            driver.find_element(By.XPATH, '//button[text()="Zoeken"]').click()
            WebDriverWait(driver, 10).until(EC.url_contains("rech_res.pl"))

            type_results = []

            # Paginate through results
            while True:
                soup = BeautifulSoup(driver.page_source, features="lxml")

                for tag in soup.find_all("div", {"class": "list"}):
                    contents = tag.find_all("div", {"class": "list-item--content"})
                    buttons = tag.find_all("div", {"class": "list-item--button"})

                    for content, button in zip(contents, buttons):
                        anchor = content.find("a", href=True)
                        pub_date = content.find("p", {"class": "list-item--date"})
                        if not anchor:
                            continue
                        type_results.append({
                            "ref_number": button.text.strip(),
                            "pub_date": pub_date.text if pub_date else "",
                            "short_text": anchor.text.strip(),
                            "url": urljoin(url_searchpage, anchor["href"]),
                            "doc_type": doc_type,
                        })
                        if max_results and len(type_results) >= max_results:
                            break

                    if max_results and len(type_results) >= max_results:
                        break

                if max_results and len(type_results) >= max_results:
                    break

                try:
                    next_btn = driver.find_element(
                        By.XPATH, "//a[@class='pagination-button pagination-next']"
                    )
                    next_btn.click()
                except Exception:
                    break

            scraping_result.extend(type_results)

    finally:
        driver.quit()

    # Fetch full text and classify each result
    total = len(scraping_result)
    for i, item in enumerate(scraping_result):
        try:
            page = requests.get(item["url"], timeout=15)
            soup = BeautifulSoup(page.text, features="lxml")
            main = soup.find("main", {"class": "page__inner page__inner--content article-text"})

            # FIX: extract ALL text, not just the first <p>
            full_text = main.get_text(separator="\n", strip=True) if main else ""
            item["long_text"] = full_text

            # Decide whether to embed and split into articles
            if is_substantive(full_text, item["doc_type"]):
                item["articles"] = split_into_articles(full_text)
                item["embed"] = True
            else:
                item["articles"] = []
                item["embed"] = False

        except Exception as e:
            item["long_text"] = ""
            item["articles"] = []
            item["embed"] = False

        if progress_callback:
            progress_callback(i + 1, total)

    return scraping_result
