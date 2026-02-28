import os
import time
import re
import argparse
import requests
import img2pdf
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:200]


def download_image(session, url, dest):
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"Failed download {url}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="FlipHTML5 downloader (URL-only).")
    parser.add_argument("url", nargs="?", help="Book URL (index.html)")
    args = parser.parse_args()

    BOOK_URL = args.url

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))

    session = requests.Session()

    try:
        driver.get(BOOK_URL)
        print("Waiting for book to initialize...")
        time.sleep(6)

        # Use the browser tab title as output directory name
        title = driver.title or urlparse(BOOK_URL).path.split('/')[-2] or 'fliphtml5_book'
        OUTPUT_DIR = sanitize_filename(title)
        if not OUTPUT_DIR:
            OUTPUT_DIR = 'fliphtml5_book'

        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        # Base URL for relative image paths
        parsed = urlparse(BOOK_URL)
        BASE_URL = f"{parsed.scheme}://{parsed.netloc}{os.path.dirname(parsed.path)}/"

        print(f"Output directory: {OUTPUT_DIR}")

        page_map = {}
        download_futures = {}

        executor = ThreadPoolExecutor(max_workers=6)

        print("Scanning DOM and downloading images as they appear...")

        no_new_count = 0
        prev_count = 0
        flips = 0

        while no_new_count < 12:
            page_divs = driver.find_elements(By.CSS_SELECTOR, "div[id^='page']")

            for div in page_divs:
                try:
                    page_id = div.get_attribute("id")
                    page_num = int(page_id.replace("page", ""))
                    if page_num in page_map:
                        continue

                    # Try to find image inside the page div
                    img_tag = div.find_element(By.TAG_NAME, "img")
                    img_src = img_tag.get_attribute("src")
                    if not img_src:
                        continue

                    # Only consider large file urls
                    if "files/large/" not in img_src and not img_src.endswith(('.jpg', '.webp', '.png')):
                        continue

                    clean_url = img_src.split('?')[0]
                    # Make absolute if needed
                    if not clean_url.startswith('http'):
                        clean_url = urljoin(BASE_URL, clean_url)

                    page_map[page_num] = clean_url

                    ext = '.webp' if '.webp' in clean_url else ('.jpg' if '.jpg' in clean_url else '.png')
                    filename = os.path.join(OUTPUT_DIR, f"{page_num:03d}{ext}")

                    if not os.path.exists(filename):
                        future = executor.submit(download_image, session, clean_url, filename)
                        download_futures[page_num] = future
                        print(f"Discovered Page {page_num} -> {os.path.basename(filename)} (queued)")

                except Exception:
                    continue

            flips += 1
            # Move forward to force rendering of later page divs
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RIGHT)
            except Exception:
                pass
            time.sleep(0.9)

            # Stop if we haven't discovered new pages for a few iterations
            if len(page_map) == prev_count:
                no_new_count += 1
            else:
                no_new_count = 0
                prev_count = len(page_map)

        # Determine total pages from discovered map
        if page_map:
            TOTAL_PAGES = max(page_map.keys())
        else:
            print("No pages discovered — exiting.")
            return

        print(f"Discovered {len(page_map)} pages, inferred total {TOTAL_PAGES}.")

        # Wait for downloads to finish and retry failed ones
        print("Awaiting downloads...")
        for pg, fut in list(download_futures.items()):
            try:
                ok = fut.result(timeout=60)
                if not ok:
                    # schedule a direct retry
                    url = page_map.get(pg)
                    ext = '.webp' if '.webp' in url else ('.jpg' if '.jpg' in url else '.png')
                    filename = os.path.join(OUTPUT_DIR, f"{pg:03d}{ext}")
                    if url:
                        download_image(session, url, filename)
            except Exception:
                url = page_map.get(pg)
                if url:
                    ext = '.webp' if '.webp' in url else ('.jpg' if '.jpg' in url else '.png')
                    filename = os.path.join(OUTPUT_DIR, f"{pg:03d}{ext}")
                    download_image(session, url, filename)

        # Ensure all pages 1..TOTAL_PAGES exist; attempt to download missing ones
        print("Verifying all pages are saved...")
        for n in range(1, TOTAL_PAGES + 1):
            expected_files = [os.path.join(OUTPUT_DIR, f"{n:03d}{ext}") for ext in ('.webp', '.jpg', '.png')]
            if not any(os.path.exists(p) for p in expected_files):
                url = page_map.get(n)
                if url:
                    ext = '.webp' if '.webp' in url else ('.jpg' if '.jpg' in url else '.png')
                    filename = os.path.join(OUTPUT_DIR, f"{n:03d}{ext}")
                    print(f"Missing page {n}, downloading now...")
                    download_image(session, url, filename)

        # Merge into PDF
        print("Merging into PDF...")
        images = [os.path.join(OUTPUT_DIR, f) for f in sorted(os.listdir(OUTPUT_DIR)) if f.endswith(('.webp', '.jpg', '.png'))]
        out_pdf = f"{OUTPUT_DIR}.pdf"
        with open(out_pdf, "wb") as f:
            f.write(img2pdf.convert(images))

        print(f"Success! PDF written to {out_pdf}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == '__main__':
    main()