"""Prepare immutable viewer data and CDN assets during a deployment build."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.request
import zipfile

from viewer import DEPLOYED, RAW, ROOT, prepare


def download_archive(item, base_url):
    name = Path(item["path"]).name
    archive = RAW / name
    expected = item["sha256"]
    if archive.exists():
        with archive.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").hexdigest() == expected:
                return archive
    temporary = archive.with_suffix(".download")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{base_url}/{item['path']}", timeout=60) as response:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
            with temporary.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != expected:
                raise RuntimeError(f"Checksum mismatch for {name}. The upstream archive has changed.")
            temporary.replace(archive)
            print(f"Downloaded and verified {name}", flush=True)
            return archive
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def extract_archive(archive):
    destination = RAW / archive.stem
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError(f"Unsafe archive path in {archive.name}")
        source.extractall(destination)


def main():
    sources = json.loads((ROOT / "data-sources.json").read_text(encoding="utf-8"))
    RAW.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        archives = list(pool.map(lambda item: download_archive(item, sources["base_url"]), sources["archives"]))
    for archive in archives:
        extract_archive(archive)

    catalog = prepare(deployment=True)
    static = ROOT / "public" / "static"
    static.mkdir(parents=True, exist_ok=True)
    for name in ["app.js", "style.css"]:
        shutil.copy2(ROOT / "viewer" / name, static / name)
    shutil.copytree(ROOT / "viewer" / "vendor", static / "vendor", dirs_exist_ok=True)
    size = sum(path.stat().st_size for path in DEPLOYED.rglob("*") if path.is_file())
    print(f"Deployment ready: {len(catalog['tiles'])} tiles, {size / 1024**2:.1f} MiB of raster data.", flush=True)


if __name__ == "__main__":
    main()
