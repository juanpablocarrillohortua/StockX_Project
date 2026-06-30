import subprocess
import time
from pathlib import Path
import sys

# Config
RANGO_INICIO = 1
RANGO_FIN = 25
DELAY_SEGUNDOS = 2

urls = [
    "https://stockx.com/brands/adidas?category=sneakers&page=",
    "https://stockx.com/brands/jordan?category=sneakers&page=",
    "https://stockx.com/brands/asics?category=sneakers&page=",
    "https://stockx.com/brands/new-balance?category=sneakers&page=",
    "https://stockx.com/brands/nike?category=sneakers&page=",
    "https://stockx.com/brands/yeezy?category=sneakers&page=",
    "https://stockx.com/brands/puma?page=",
    ]

for url_base in urls:
    marca = url_base.split("/brands/")[1].split("?")[0]
    print(f"\n=========================================")
    print(f"🚀 INITIALIZING MARQUEE PROCESSING: {marca.upper()}")
    print(f"=========================================")
    for i in range(RANGO_INICIO, RANGO_FIN + 1):
        print(f"\n--- Initializing scraping for page {i} ---")

        url_dinamica = f"{url_base}{i}"

        comando = [sys.executable, "main.py", "--url", url_dinamica]

        try:
            resultado = subprocess.run(comando, check=True, text=True)
            print(f"Page {i} processed successfully.")
        except subprocess.CalledProcessError as e:
            print(f"Error processing page {i}: {e}")

        if i < RANGO_FIN:
            print(f"Throttling request: Sleeping for {DELAY_SEGUNDOS} seconds before the next page...")
            time.sleep(DELAY_SEGUNDOS)

print("\n Automation pipeline finalized successfully!")