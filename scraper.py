import os
import time
import json
import requests
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys

# Configuration
BOOK_URL = "https://online.fliphtml5.com/ltcgo/bcfl/index.html"
OUTPUT_DIR = "SMK_Seremban_Verified"
TOTAL_PAGES = 148

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# Setup Chrome with Performance Logging
chrome_options = Options()
chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def get_new_urls(existing_set):
    """Peek at the logs and find any new high-res image URLs."""
    new_urls = []
    logs = driver.get_log("performance")
    for entry in logs:
        log = json.loads(entry["message"])["message"]
        if log["method"] == "Network.requestWillBeSent":
            url = log["params"]["request"]["url"]
            if "/files/large/" in url and (".webp" in url or ".jpg" in url):
                clean_url = url.split('?')[0]
                if clean_url not in existing_set:
                    new_urls.append(clean_url)
    return new_urls

try:
    driver.get(BOOK_URL)
    time.sleep(5)
    
    all_captured = []
    seen_urls = set()

    print("Starting ordered capture...")
    # We step through 2 pages at a time (since it's a spread)
    for step in range(0, TOTAL_PAGES, 2):
        print(f"Capturing spread starting at page {step + 1}...")
        
        # Trigger the flip
        driver.find_element("tag name", "body").send_keys(Keys.RIGHT)
        time.sleep(random.uniform(1.6, 2.2)) # Wait for images to actually request
        
        # Capture whatever URLs were just requested
        new_batch = get_new_urls(seen_urls)
        for url in new_batch:
            all_captured.append(url)
            seen_urls.add(url)

    # Download them in the order they were captured
    for i, url in enumerate(all_captured):
        page_num = i + 1
        ext = ".webp" if ".webp" in url else ".jpg"
        filename = os.path.join(OUTPUT_DIR, f"{page_num:03d}{ext}")
        
        r = requests.get(url)
        with open(filename, "wb") as f:
            f.write(r.content)
        print(f"Saved Page {page_num}")

finally:
    driver.quit()