import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

def download_file(url, dest_folder="."):
    """
    Downloads a file from the given URL and saves it to the destination folder.
    """
    filename = url.split("/")[-1]
    filepath = os.path.join(dest_folder, filename)

    try:
        print(f"Downloading: {filename}")
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        print(f"Saved: {filepath}")
    except Exception as e:
        print(f"Failed to download: {url} | Error: {e}")

def download_all_from_file(file_path, max_workers=4):
    """
    Reads URLs from a file and downloads them concurrently using a thread pool.
    """
    with open(file_path, "r") as file:
        urls = [line.strip() for line in file if line.strip()]

    # Filter valid URLs only
    valid_urls = [url for url in urls if url.startswith("https://") and url.endswith("champsimtrace.xz")]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(download_file, url): url for url in valid_urls}
        for future in as_completed(future_to_url):
            future.result()  # Trigger exception if any

if __name__ == "__main__":
    download_all_from_file("trace_url.txt", max_workers=10)
