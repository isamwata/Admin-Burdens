# -*- coding: utf-8 -*-
"""
Created on Fri Jun 21 13:12:35 2024

@author: lucp8733
"""
import json

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bs4 import BeautifulSoup
import requests

from datetime import datetime
import pandas as pd

from tqdm import tqdm

with open('config.json') as cfile:
    config_data = json.load(cfile)

# difference between each date. D means one day
D = config_data['scraping']['scraping_interval']
datetime_format = config_data['scraping']['datetime_format']
# start date
init_date = datetime.strptime(config_data['scraping']['start_date'], datetime_format)
close_date = datetime.strptime(config_data['scraping']['end_date'], datetime_format)


startdate_list = pd.date_range(init_date, close_date, freq=D+"S")
enddate_list = pd.date_range(init_date, close_date, freq=D+"E")

url_staatsblad = config_data['scraping']['url_searchpage']
url_detail_search = config_data['scraping']['url_detail_page']
document_types = config_data['scraping']['document_types']

driver = webdriver.Chrome()

scraping_result = list()

for doc_type in document_types:

    driver.get(url_staatsblad)
    element = driver.find_element(By.XPATH, "//select[@name='dt']")
    all_options = element.find_elements(By.TAG_NAME, "option")
    all_options = [opt.text for opt in all_options]
    
    if doc_type not in all_options:
        print("Document type " + doc_type  + " must be one of: ")
        for opt in all_options:
            print("--" + opt)
        continue
    
    
                              
    for start_dt, end_dt in zip(startdate_list, enddate_list):
        
        driver.get(url_staatsblad)
        
        select_dt = Select(driver.find_element(By.XPATH, "//select[@name='dt']"))
        select_dt.select_by_value(doc_type)
    
        formatted_startdate = start_dt.strftime(datetime_format)
        formatted_enddate = end_dt.strftime(datetime_format)
        
        start_date = driver.find_element(By.XPATH, "//input[@name='pdd']")
        driver.execute_script("arguments[0].value = arguments[1]", start_date, start_dt.strftime("%Y-%m-%d"))

        end_date = driver.find_element(By.XPATH, "//input[@name='pdf']")
        driver.execute_script("arguments[0].value = arguments[1]", end_date, end_dt.strftime("%Y-%m-%d"))
        
        zoeken_knop = driver.find_element(By.XPATH, '//button[text()="Zoeken"]')
        zoeken_knop.click()

        # Wait for results page to load
        WebDriverWait(driver, 10).until(
            EC.url_contains("rech_res.pl")
        )

        scrape_page = True
        page_num = 0

        while scrape_page:
            html = driver.page_source

            # Save first results page for debugging
            if page_num == 0:
                with open("debug_results.html", "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"[DEBUG] Results page saved to debug_results.html")

            page_num += 1
            soup = BeautifulSoup(html, features="lxml")

            # Debug: print all div classes found
            all_divs = soup.find_all("div", class_=True)
            div_classes = set()
            for d in all_divs:
                for c in d.get("class", []):
                    div_classes.add(c)
            print(f"[DEBUG] Div classes on page: {sorted(div_classes)}")

            for tag in soup.find_all("div", {"class": "list"}):
                
                list_item_contents = tag.find_all("div", {"class": "list-item--content"})
                list_item_buttons = tag.find_all("div", {"class": "list-item--button"})
                
                for list_item, list_button in zip(list_item_contents, list_item_buttons):
                    item = list_item.find("a", href=True)
                    pub_date = list_item.find("p", {"class": "list-item--date"})
                    href = item['href']
                    id_number = list_button.text.strip()
                    detailed_ref = url_detail_search + href
                    
                    short_text = item.text.strip()
                    
                    item_info = {
                        'ref_number': id_number,
                        'pub_date': pub_date.text,
                        'short_text': short_text,
                        'url': detailed_ref
                        }
                    
                    scraping_result.append(item_info)
            
            try:
                nextbutton = driver.find_element(By.XPATH, "//a[@class='pagination-button pagination-next']")
            except: 
                nextbutton = None
            
            if nextbutton is not None:
                nextbutton.click()
            else:
                scrape_page = False

driver.close()

for item_info in tqdm(scraping_result, position = 0, leave = True):
    detailed_ref = item_info['url']
    detailed_page = requests.get(detailed_ref)
    detailed_soup = BeautifulSoup(detailed_page.text, features="lxml")
    list_item_detail = detailed_soup.find("main", {"class": "page__inner page__inner--content article-text"})
    long_text = list_item_detail.find("p")
    item_info['long_text'] = long_text.text
    
scraping_result_df = pd.DataFrame.from_dict(scraping_result)
scraping_result_df.to_excel(config_data['scraping']['output_location'] + "/" + str(init_date.strftime(datetime_format)) + "_" +  str(close_date.strftime(datetime_format)) +"_"  + "_".join(document_types) + "__scraping_results.xlsx")