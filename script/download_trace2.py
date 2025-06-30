import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_remote_file_size(url):
    """
    Returns the size of the file from the Content-Length header.
    """
    try:
        response = requests.head(url, allow_redirects=True)
        response.raise_for_status()
        return int(response.headers.get('Content-Length', -1))
    except Exception as e:
        print(f"Failed to get size for: {url} | Error: {e}")
        return -1

def download_file(url, dest_folder="."):
    """
    Downloads a file from the given URL and saves it to the destination folder,
    only if not already downloaded with correct size.
    """
    filename = url.split("/")[-1]
    filepath = os.path.join(dest_folder, filename)

    remote_size = get_remote_file_size(url)
    if remote_size == -1:
        print(f"Skipping (cannot determine size): {filename}")
        return

    if os.path.exists(filepath):
        local_size = os.path.getsize(filepath)
        if local_size == remote_size:
            print(f"Already downloaded correctly: {filename}")
            return
        else:
            print(f"Incomplete or corrupted file detected: {filename} (local: {local_size}, remote: {remote_size}), re-downloading...")

    try:
        print(f"Downloading: {filename}")
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Verify download
        if os.path.getsize(filepath) != remote_size:
            print(f"Download failed (size mismatch): {filename}")
        else:
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
            future.result()

if __name__ == "__main__":
    download_all_from_file("trace_url.txt", max_workers=10)

