import os
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
    if chromedriver_path:
        from selenium.webdriver.chrome.service import Service
        return webdriver.Chrome(service=Service(chromedriver_path), options=options)

    return webdriver.Chrome(options=options)


def scrape_documents(start_date: datetime, end_date: datetime, doc_types: list,
                     url_searchpage: str, url_detail_page: str,
                     progress_callback=None):
    driver = get_driver()
    scraping_result = []

    try:
        for doc_type in doc_types:
            # Verify document type exists on the site
            driver.get(url_searchpage)
            element = driver.find_element(By.XPATH, "//select[@name='dt']")
            all_options = [opt.text for opt in element.find_elements(By.TAG_NAME, "option")]

            if doc_type not in all_options:
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

            # Paginate through all results
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
                        scraping_result.append({
                            "ref_number": button.text.strip(),
                            "pub_date": pub_date.text if pub_date else "",
                            "short_text": anchor.text.strip(),
                            "url": url_detail_page + anchor["href"],
                        })

                try:
                    next_btn = driver.find_element(
                        By.XPATH, "//a[@class='pagination-button pagination-next']"
                    )
                    next_btn.click()
                except Exception:
                    break
    finally:
        driver.quit()

    # Fetch full text for each result
    total = len(scraping_result)
    for i, item in enumerate(scraping_result):
        try:
            page = requests.get(item["url"], timeout=15)
            soup = BeautifulSoup(page.text, features="lxml")
            main = soup.find("main", {"class": "page__inner page__inner--content article-text"})
            p = main.find("p") if main else None
            item["long_text"] = p.text if p else ""
        except Exception:
            item["long_text"] = ""

        if progress_callback:
            progress_callback(i + 1, total)

    return scraping_result
