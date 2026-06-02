"""Download NYC Yellow Taxi parquet files (Jan–Jun 2023) from TLC CDN."""
import os
import sys
import requests

BASE_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data"
ZONE_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MONTHS = range(1, 7)  # Jan–Jun 2023


def download_file(url: str, dest: str) -> None:
    if os.path.exists(dest):
        print(f"  skip (exists): {os.path.basename(dest)}")
        return
    print(f"  downloading: {os.path.basename(dest)} ...", end="", flush=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  downloading: {os.path.basename(dest)} ... {pct}%", end="", flush=True)
    print(f"\r  done: {os.path.basename(dest)} ({downloaded // 1024 // 1024} MB)")


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

    print("=== Downloading NYC Yellow Taxi 2023 (Jan–Jun) ===")
    for m in MONTHS:
        fname = f"yellow_tripdata_2023-{m:02d}.parquet"
        download_file(f"{BASE_URL}/{fname}", os.path.join(DATA_DIR, fname))

    print("\n=== Downloading zone lookup CSV ===")
    download_file(ZONE_URL, os.path.join(DATA_DIR, "taxi_zone_lookup.csv"))

    print("\nAll files downloaded.")


if __name__ == "__main__":
    main()
