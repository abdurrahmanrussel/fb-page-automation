"""
Download images by search keyword.

Usage:
    python download_images.py "mango fruit" 100
    python download_images.py "রাজশাহীর আম" 50

Images saved to: downloaded_images/<keyword>/
Then upload that folder to your Google Drive image folder.
"""
import os
import sys


def download(keyword: str, count: int) -> None:
    try:
        from icrawler.builtin import BingImageCrawler, GoogleImageCrawler
    except ImportError:
        print("Installing icrawler...")
        os.system(f"{sys.executable} -m pip install icrawler")
        from icrawler.builtin import BingImageCrawler, GoogleImageCrawler

    safe_name = keyword.replace(" ", "_").replace("/", "_")[:50]
    save_dir  = os.path.join(os.path.dirname(__file__), "downloaded_images", safe_name)
    os.makedirs(save_dir, exist_ok=True)

    print(f'\nSearching: "{keyword}"')
    print(f"Target: {count} images")
    print(f"Saving to: {save_dir}\n")

    # Try Bing first (more reliable), fall back to Google
    try:
        crawler = BingImageCrawler(
            storage={"root_dir": save_dir},
            downloader_threads=4,
        )
        crawler.crawl(keyword=keyword, max_num=count, min_size=(300, 300))
    except Exception as e:
        print(f"Bing failed ({e}), trying Google...")
        crawler = GoogleImageCrawler(
            storage={"root_dir": save_dir},
            downloader_threads=4,
        )
        crawler.crawl(keyword=keyword, max_num=count, min_size=(300, 300))

    files = [f for f in os.listdir(save_dir) if not f.startswith(".")]
    print(f"\n✅ Downloaded {len(files)} images to: {save_dir}")
    print("Upload this folder to your Google Drive image folder when done.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python download_images.py \"search topic\" [count]")
        print('Example: python download_images.py "mango fruit" 100')
        return

    keyword = sys.argv[1]
    count   = int(sys.argv[2]) if len(sys.argv) > 2 else 50

    download(keyword, count)


if __name__ == "__main__":
    main()
