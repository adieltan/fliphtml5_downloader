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
from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.common.action_chains import ActionChains
import base64
import mimetypes


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


def remove_overlays(driver):
    """Try to remove common ad/popup overlays and dismiss JS alerts."""
    try:
        js = '''
        try{
            var sel = '[id*="ad"], [class*="ad"], [id*="Ad"], [class*="Ad"], .overlay, .modal, .popup, .cookie, .subscription, .subscribe, .sb-close, .close, .close-btn';
            document.querySelectorAll(sel).forEach(function(n){n.remove();});
            document.querySelectorAll('iframe').forEach(function(f){
                var src = f.src || '';
                if(/ad|doubleclick|googlesyndication|adservice|adroll|taboola/i.test(src)){
                    f.remove();
                }
            });
            // attempt to click common close buttons
            document.querySelectorAll('button, a').forEach(function(b){
                var t = (b.innerText||'').toLowerCase();
                if(t.indexOf('close')!==-1 || t==='×' || t==='x' || t.indexOf('dismiss')!==-1){
                    try{ b.click(); }catch(e){}
                }
            });
        }catch(e){}
        '''
        driver.execute_script(js)
    except Exception:
        pass

    try:
        alert = driver.switch_to.alert
        alert.dismiss()
    except Exception:
        # ignore if no alert present
        pass


def attempt_flip(driver):
    """Attempt multiple strategies to advance the viewer to the next page."""
    try:
        # 1) try to click common "next" buttons
        next_selectors = ['.next', '.btn-next', '.flip-next', '.nextPage', '.page-next', '[aria-label="Next"]', '.control-next', '.nav-next']
        for sel in next_selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    try:
                        el.click()
                        return True
                    except Exception:
                        pass
            except Exception:
                continue

        # 2) send ArrowRight via ActionChains (more reliable than element.send_keys)
        try:
            ActionChains(driver).send_keys(Keys.ARROW_RIGHT).perform()
            return True
        except Exception:
            pass

        # 3) dispatch keyboard event via JS
        try:
            js = "var e = new KeyboardEvent('keydown', {key:'ArrowRight', keyCode:39, which:39, code:'ArrowRight', bubbles:true}); document.dispatchEvent(e);"
            driver.execute_script(js)
            return True
        except Exception:
            pass

        # 4) click the main viewer area to ensure focus then try arrow key on body
        try:
            body = driver.find_element(By.TAG_NAME, 'body')
            body.click()
            ActionChains(driver).send_keys(Keys.ARROW_RIGHT).perform()
            return True
        except Exception:
            pass

        # 5) try a drag/swipe gesture on the viewer area (right->left)
        try:
            viewer_selectors = ['.viewer', '.flipbookViewport', '.flipbook-viewport', '.pageContainer', '.viewer-container', '#viewer', '.book', '.page']
            for sel in viewer_selectors:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if not el or not el.is_displayed():
                        continue
                    size = el.size
                    w = int(size.get('width', 0))
                    h = int(size.get('height', 0))
                    if w < 20 or h < 20:
                        continue
                    # start near right edge, mid height
                    start_x = int(w * 0.8)
                    start_y = int(h * 0.5)
                    move_x = -int(w * 0.6)
                    # perform click-and-drag
                    ActionChains(driver).move_to_element_with_offset(el, start_x, start_y).click_and_hold().pause(0.05).move_by_offset(move_x, 0).pause(0.1).release().perform()
                    time.sleep(0.2)
                    return True
                except Exception:
                    continue
        except Exception:
            pass

    except Exception:
        return False

    return False


def pick_src_from_srcset(srcset: str) -> str:
    """Choose the best candidate from a srcset string (prefer largest width)."""
    if not srcset:
        return ''
    parts = [p.strip() for p in srcset.split(',') if p.strip()]
    best = ''
    best_w = 0
    for p in parts:
        sub = p.split()
        url = sub[0]
        w = 0
        if len(sub) > 1:
            try:
                if sub[1].endswith('w'):
                    w = int(sub[1][:-1])
                elif sub[1].endswith('x'):
                    # treat pixel density as multiplier; approximate
                    w = int(float(sub[1][:-1]) * 1000)
            except Exception:
                w = 0
        if w >= best_w:
            best_w = w
            best = url
    if not best and parts:
        best = parts[-1].split()[0]
    return best


def save_data_url(data_url: str, filename: str) -> bool:
    try:
        header, encoded = data_url.split(',', 1)
        if header.startswith('data:'):
            # header like data:image/png;base64
            is_base64 = ';base64' in header
            if is_base64:
                b = base64.b64decode(encoded)
                with open(filename, 'wb') as f:
                    f.write(b)
                return True
        return False
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="FlipHTML5 downloader (URL-only).")
    parser.add_argument("url", nargs="?", help="Book URL (index.html)")
    args = parser.parse_args()

    BOOK_URL = args.url

    options = webdriver.ChromeOptions()
    prefs = {
        "profile.default_content_setting_values.notifications": 2,
        "profile.default_content_setting_values.popups": 2,
        "profile.default_content_setting_values.geolocation": 2
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument("--mute-audio")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    session = requests.Session()

    try:
        driver.get(BOOK_URL)
        print("Waiting for book to initialize...")
        time.sleep(6)
        # remove any initial overlays/popups/ads that appear on load
        remove_overlays(driver)

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
            # periodically clear overlays/ads that may appear while scanning
            remove_overlays(driver)
            start_count = len(page_map)
            page_divs = driver.find_elements(By.CSS_SELECTOR, "div[id^='page'], div[id^='pageMask']")

            for div in page_divs:
                try:
                    page_id = (div.get_attribute("id") or "")
                    m = re.search(r"(\d+)", page_id)
                    if not m:
                        continue
                    page_num = int(m.group(1))
                    if page_num in page_map:
                        continue

                    # Try to find image inside the page div (handle lazy-loaded attributes and background-image)
                    img_src = None
                    try:
                        img_tag = div.find_element(By.TAG_NAME, "img")
                        # common attributes where lazy-src may be stored
                        for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-url", "data-srcset"):
                            val = img_tag.get_attribute(attr)
                            if val:
                                img_src = val
                                break
                    except Exception:
                        img_tag = None

                    # if still not found, check for inline style background-image
                    if not img_src:
                        try:
                            # check children or the div itself for background-image
                            elems = div.find_elements(By.CSS_SELECTOR, "[style*='background']")
                            for e in elems:
                                style = e.get_attribute('style') or ''
                                m = re.search(r"background(?:-image)?:\s*url\((?:'|\")?(.*?)(?:'|\")?\)", style)
                                if m:
                                    img_src = m.group(1)
                                    break
                        except Exception:
                            pass

                    if not img_src:
                        # try to find any image descendants
                        try:
                            imgs = div.find_elements(By.TAG_NAME, 'img')
                            for it in imgs:
                                val = it.get_attribute('src') or it.get_attribute('data-src')
                                if val:
                                    img_src = val
                                    break
                        except Exception:
                            pass

                    # If still not found, check for <picture> <source> with srcset
                    if not img_src:
                        try:
                            sources = div.find_elements(By.CSS_SELECTOR, 'picture source, source')
                            for s in sources:
                                ss = s.get_attribute('srcset') or s.get_attribute('data-srcset')
                                if ss:
                                    cand = pick_src_from_srcset(ss)
                                    if cand:
                                        img_src = cand
                                        break
                        except Exception:
                            pass

                    # If still not found, try canvases inside the div (rendered pages)
                    if not img_src:
                        try:
                            canvases = div.find_elements(By.TAG_NAME, 'canvas')
                            for c in canvases:
                                try:
                                    data_url = driver.execute_script('return arguments[0].toDataURL("image/png");', c)
                                    if data_url and data_url.startswith('data:'):
                                        ext = '.png'
                                        filename = os.path.join(OUTPUT_DIR, f"{page_num:03d}{ext}")
                                        if save_data_url(data_url, filename):
                                            page_map[page_num] = filename
                                            print(f"Discovered Page {page_num} -> {os.path.basename(filename)} (canvas)")
                                            img_src = None
                                            break
                                except Exception:
                                    continue
                            # if we saved from canvas, skip further processing
                            if page_num in page_map and page_map[page_num].endswith('.png') and os.path.exists(page_map[page_num]):
                                continue
                        except Exception:
                            pass

                    # If no image was found inside this page div, try a proximity fallback:
                    # look for any <img> on the page whose bounding box overlaps this div.
                    if not img_src:
                        try:
                            div_loc = div.location
                            div_size = div.size
                            div_left = div_loc.get('x', 0)
                            div_top = div_loc.get('y', 0)
                            div_right = div_left + div_size.get('width', 0)
                            div_bottom = div_top + div_size.get('height', 0)
                            imgs_all = driver.find_elements(By.TAG_NAME, 'img')
                            for it in imgs_all:
                                try:
                                    it_loc = it.location
                                    it_size = it.size
                                    it_left = it_loc.get('x', 0)
                                    it_top = it_loc.get('y', 0)
                                    it_right = it_left + it_size.get('width', 0)
                                    it_bottom = it_top + it_size.get('height', 0)
                                    # check overlap
                                    horiz_overlap = max(0, min(div_right, it_right) - max(div_left, it_left))
                                    vert_overlap = max(0, min(div_bottom, it_bottom) - max(div_top, it_top))
                                    if horiz_overlap > 2 and vert_overlap > 2:
                                        # consider this image as belonging to the div
                                        for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-url", "data-srcset"):
                                            val = it.get_attribute(attr)
                                            if val:
                                                img_src = val
                                                break
                                        if img_src:
                                            break
                                except Exception:
                                    continue
                        except Exception:
                            pass

                    if not img_src:
                        continue

                    # handle srcset values
                    if ' ' in img_src and ',' in img_src:
                        candidate = pick_src_from_srcset(img_src)
                        if candidate:
                            img_src = candidate

                    # Normalize src (remove query strings)
                    clean_url = img_src.split('?')[0]
                    # Some src values start with '//' — make absolute
                    if clean_url.startswith('//'):
                        parsed = urlparse(BOOK_URL)
                        clean_url = f"{parsed.scheme}:{clean_url}"

                    # Make absolute relative to the page URL if needed
                    if not clean_url.startswith('http') and not clean_url.startswith('data:'):
                        clean_url = urljoin(BOOK_URL, clean_url)

                    # Accept common image paths (not only files/large) or data URLs
                    if not (clean_url.startswith('data:') or any(x in clean_url for x in ('/files/', '.jpg', '.webp', '.png', '.jpeg'))):
                        # skip non-image assets
                        continue

                    # If it's a data URL (canvas or inline), save directly
                    ext = '.png' if '.png' in clean_url or clean_url.startswith('data:image/png') else ('.webp' if '.webp' in clean_url else ('.jpg' if '.jpg' in clean_url or '.jpeg' in clean_url else '.png'))
                    filename = os.path.join(OUTPUT_DIR, f"{page_num:03d}{ext}")

                    if clean_url.startswith('data:'):
                        if save_data_url(clean_url, filename):
                            page_map[page_num] = filename
                            print(f"Discovered Page {page_num} -> {os.path.basename(filename)} (saved data URL)")
                            continue
                        else:
                            # couldn't save data URL, continue to next method
                            pass

                    # schedule download for http(s) URLs
                    page_map[page_num] = clean_url
                    if not os.path.exists(filename):
                        future = executor.submit(download_image, session, clean_url, filename)
                        download_futures[page_num] = future
                        print(f"Discovered Page {page_num} -> {os.path.basename(filename)} (queued)")

                except Exception:
                    continue

            flips += 1
            # If this iteration found no new pages, run a quick fallback scan of all <img> elements
            if len(page_map) == start_count:
                try:
                    imgs = driver.find_elements(By.TAG_NAME, 'img')
                    for it in imgs:
                        src = None
                        for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-url", "data-srcset"):
                            val = it.get_attribute(attr)
                            if val:
                                src = val
                                break
                        if not src:
                            continue
                        # pick from srcset if needed
                        if ' ' in src and ',' in src:
                            cand = pick_src_from_srcset(src)
                            if cand:
                                src = cand
                        clean_url = src.split('?')[0]
                        if clean_url.startswith('//'):
                            parsed = urlparse(BOOK_URL)
                            clean_url = f"{parsed.scheme}:{clean_url}"
                        if not clean_url.startswith('http') and not clean_url.startswith('data:'):
                            clean_url = urljoin(BOOK_URL, clean_url)
                        if not (clean_url.startswith('data:') or any(x in clean_url for x in ('/files/', '.jpg', '.webp', '.png', '.jpeg'))):
                            continue
                        if clean_url in page_map.values():
                            continue
                        # assign to next missing page index
                        next_page = max(page_map.keys()) + 1 if page_map else 1
                        while next_page in page_map:
                            next_page += 1
                        page_map[next_page] = clean_url
                        ext = '.webp' if '.webp' in clean_url else ('.jpg' if '.jpg' in clean_url else '.png')
                        filename = os.path.join(OUTPUT_DIR, f"{next_page:03d}{ext}")
                        if clean_url.startswith('data:'):
                            save_data_url(clean_url, filename)
                            print(f"Fallback (iter) added Page {next_page} -> {os.path.basename(filename)} (saved data URL)")
                        else:
                            future = executor.submit(download_image, session, clean_url, filename)
                            download_futures[next_page] = future
                            print(f"Fallback (iter) added Page {next_page} -> {os.path.basename(filename)} (queued)")
                except Exception:
                    pass

            # Move forward to force rendering of later page divs
            try:
                attempt_flip(driver)
            except Exception:
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

        # Fallback: only scan all <img> elements if we discovered no pages earlier
        if not page_map:
            print("Fallback: scanning all <img> elements for additional pages...")
            try:
                imgs = driver.find_elements(By.TAG_NAME, 'img')
                for it in imgs:
                    src = None
                    for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-url", "data-srcset"):
                        val = it.get_attribute(attr)
                        if val:
                            src = val
                            break
                    if not src:
                        continue
                    clean_url = src.split('?')[0]
                    if clean_url.startswith('//'):
                        parsed = urlparse(BOOK_URL)
                        clean_url = f"{parsed.scheme}:{clean_url}"
                    if not clean_url.startswith('http'):
                        clean_url = urljoin(BOOK_URL, clean_url)
                    if not any(x in clean_url for x in ('/files/', '.jpg', '.webp', '.png', '.jpeg')):
                        continue
                    if clean_url in page_map.values():
                        continue
                    next_page = max(page_map.keys()) + 1 if page_map else 1
                    while next_page in page_map:
                        next_page += 1
                    page_map[next_page] = clean_url
                    ext = '.webp' if '.webp' in clean_url else ('.jpg' if '.jpg' in clean_url else '.png')
                    filename = os.path.join(OUTPUT_DIR, f"{next_page:03d}{ext}")
                    if not os.path.exists(filename):
                        future = executor.submit(download_image, session, clean_url, filename)
                        download_futures[next_page] = future
                        print(f"Fallback added Page {next_page} -> {os.path.basename(filename)} (queued)")
            except Exception:
                pass
        else:
            print("Skipping fallback: images already discovered from page elements.")

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