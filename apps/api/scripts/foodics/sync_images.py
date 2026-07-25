"""
Mirror Foodics product and category photos into our own bucket.

Foodics serves images from its own S3. Pointing our catalogue at those URLs
would leave the menu dependent on a system we are replacing, and the URLs stop
resolving the moment the Foodics account lapses. This copies each image once,
keyed by a hash of the source URL so re-runs are free, and writes a manifest
mapping the Foodics URL to ours for the catalogue importer to consume.

    python scripts/foodics/sync_images.py export.json --bucket mm-product-images

Requires gcloud to be authenticated with write access to the bucket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

#: Foodics occasionally serves an image whose extension does not match its
#: content. The extension decides the Content-Type we set on the object, and a
#: wrong one makes browsers download rather than render it.
_EXTENSION_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def object_name(url: str, content_type: str | None) -> str:
    """A stable name for the copy of `url`, so re-runs overwrite in place."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:32]
    suffix = _EXTENSION_BY_TYPE.get(
        (content_type or "").split(";")[0].strip(),
        pathlib.PurePosixPath(url.split("?")[0]).suffix.lower() or ".jpg",
    )
    return f"menu/{digest}{suffix}"


def collect_urls(export: dict) -> list[str]:
    urls: list[str] = []
    for group in ("categories", "products"):
        for row in export.get(group, []):
            image = row.get("image")
            if image and image.startswith("http"):
                urls.append(image)
    # Order-preserving dedupe: the same photo is often reused across products.
    return list(dict.fromkeys(urls))


def download(url: str) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "mm-importer/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(), response.headers.get("Content-Type")


def upload(data: bytes, bucket: str, name: str, content_type: str) -> str:
    """Write one object via gcloud, which already holds the user's credentials."""
    destination = f"gs://{bucket}/{name}"
    subprocess.run(
        [
            "gcloud",
            "storage",
            "cp",
            "-",
            destination,
            f"--content-type={content_type}",
            "--cache-control=public, max-age=31536000, immutable",
        ],
        input=data,
        check=True,
        capture_output=True,
    )
    return f"https://storage.googleapis.com/{bucket}/{name}"


def sync_one(url: str, bucket: str) -> tuple[str, str | None, str | None]:
    try:
        data, content_type = download(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return url, None, f"download failed: {exc}"

    name = object_name(url, content_type)
    guessed = content_type or mimetypes.guess_type(name)[0] or "image/jpeg"
    try:
        return url, upload(data, bucket, name, guessed.split(";")[0].strip()), None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode()[-200:]
        return url, None, f"upload failed: {detail}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=pathlib.Path, help="Foodics export JSON")
    parser.add_argument("--bucket", required=True)
    parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        help="Where to write the source→hosted URL map (default: alongside export)",
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    export = json.loads(args.export.read_text())
    urls = collect_urls(export)
    manifest_path = args.manifest or args.export.with_name("image_manifest.json")

    # Re-runs skip whatever already succeeded, so a partial failure is cheap to
    # retry and a full re-run costs nothing.
    manifest: dict[str, str] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    todo = [u for u in urls if u not in manifest]

    print(f"{len(urls)} images referenced, {len(todo)} to copy")
    failures: list[str] = []
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for i, (url, hosted, error) in enumerate(
                pool.map(lambda u: sync_one(u, args.bucket), todo), start=1
            ):
                if hosted:
                    manifest[url] = hosted
                else:
                    failures.append(f"{url}: {error}")
                if i % 10 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}")

    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print(f"manifest: {manifest_path} ({len(manifest)} entries)")

    if failures:
        print(f"\n{len(failures)} failed:")
        for f in failures[:20]:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
