import os
import re
import json
import requests

# Based on your previous data
BASE_URL = "https://online.fliphtml5.com/ltcgo/bcfl/"
CONFIG_URL = f"{BASE_URL}javascript/config.js"
OUTPUT_DIR = "SMK_Seremban_Ordered"

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def download_ordered():
    print(f"Fetching configuration to get the correct page sequence...")
    response = requests.get(CONFIG_URL)
    
    if response.status_code != 200:
        print("Error: Could not access config.js")
        return

    # Extract the 'pageGraphic' array which holds the filenames in order
    content = response.text
    # We look for the "pageGraphic" : [ "hash1.webp", "hash2.webp" ... ]
    match = re.search(r'\"pageGraphic\"\s*:\s*(\[.*?\])', content, re.DOTALL)
    
    if not match:
        print("Error: Could not find the page sequence in the source code.")
        return
        
    # Convert the string list into a Python list
    ordered_hashes = json.loads(match.group(1))
    print(f"Found {len(ordered_hashes)} pages in the correct sequence.")

    for i, hash_name in enumerate(ordered_hashes):
        page_num = i + 1
        img_url = f"{BASE_URL}files/large/{hash_name}"
        
        # Download the image
        try:
            img_data = requests.get(img_url).content
            # We save it as 001.webp, 002.webp etc. to keep them sorted in your folder
            file_path = os.path.join(OUTPUT_DIR, f"{page_num:03d}.webp")
            
            with open(file_path, "wb") as f:
                f.write(img_data)
            
            print(f"Downloaded Page {page_num}: {hash_name}")
        except Exception as e:
            print(f"Failed to download page {page_num}: {e}")

if __name__ == "__main__":
    download_ordered()