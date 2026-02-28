import os
import time
import requests
import img2pdf
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By

# Config
BOOK_URL = "https://online.fliphtml5.com/ltcgo/bcfl/index.html"
OUTPUT_DIR = "SMK_Seremban_Verified_Order"
TOTAL_PAGES = 148
BASE_URL = "https://online.fliphtml5.com/ltcgo/bcfl/"

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))

try:
    driver.get(BOOK_URL)
    print("Waiting for book to initialize...")
    time.sleep(8) # Extra time for the cover and first few pages to manifest in DOM
    
    # Dictionary to store {page_number: image_url}
    page_map = {}

    print("Scanning DOM for page IDs...")
    # We flip through the book to make sure all page DIVs are generated in the HTML
    for _ in range((TOTAL_PAGES // 2) + 5):
        # Find all divs that look like id="page1", id="page2", etc.
        page_divs = driver.find_elements(By.CSS_SELECTOR, "div[id^='page']")
        
        for div in page_divs:
            try:
                page_id = div.get_attribute("id") # e.g., "page2"
                page_num = int(page_id.replace("page", ""))
                
                # Find the img tag inside this specific page div
                img_tag = div.find_element(By.TAG_NAME, "img")
                img_src = img_tag.get_attribute("src")
                
                if img_src and "files/large/" in img_src:
                    clean_url = img_src.split('?')[0]
                    if page_num not in page_map:
                        page_map[page_num] = clean_url
                        print(f"Mapped Page {page_num} -> {clean_url.split('/')[-1]}")
            except:
                continue
        
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RIGHT)
        time.sleep(1.5)

    # Download in strict numerical order
    print(f"\nDownloading {len(page_map)} pages in correct sequence...")
    for n in range(1, TOTAL_PAGES + 1):
        if n in page_map:
            url = page_map[n]
            # Ensure URL is absolute
            if not url.startswith("http"):
                url = BASE_URL + url
                
            ext = ".webp" if ".webp" in url else ".jpg"
            filename = os.path.join(OUTPUT_DIR, f"{n:03d}{ext}")
            
            r = requests.get(url)
            with open(filename, "wb") as f:
                f.write(r.content)
            print(f"Confirmed & Saved: Page {n}")

    # Generate PDF
    print("\nMerging into PDF...")
    images = [os.path.join(OUTPUT_DIR, f) for f in sorted(os.listdir(OUTPUT_DIR)) if f.endswith(('.webp', '.jpg'))]
    with open("SMK_Seremban_2023_Final_Ordered.pdf", "wb") as f:
        f.write(img2pdf.convert(images))
    
    print("Success! PDF is ready.")

finally:
    driver.quit()