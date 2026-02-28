# fliphtml5 downloader

Small utility to download FlipHTML5 books (images) and merge them into a PDF.

Prerequisites
- Python 3.9+ (virtualenv recommended)
- Chrome or Chromium installed

Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Usage

- Run the scraper with a book URL (it will auto-pick the tab title as the output directory and download pages concurrently):

```bash
python fliphtml5/scraper3.py "https://fliphtml5.com/wrgqd/uplf"
```

Output
- A folder is created with the browser tab title (sanitized) containing downloaded images.
- A merged PDF named `<output_dir>_final.pdf` is written when complete.

Notes
- This tool uses Selenium + webdriver-manager to drive Chrome — the first run will download a chromedriver.

License
- See the LICENSE file.
