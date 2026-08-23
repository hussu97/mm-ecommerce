"""Upload the courier badge PNGs to the GCS image bucket, once.

The badges in `scripts/courier_logos/*.png` are uniform 256x256 brand marks —
one per courier code (`talabat`, `noon_food`, `noon_send`, `lalamove`, …). This
puts each at `couriers/{code}.png` in the same public GCS bucket
(`mm-product-images`) the storefront's product images live in, but under its own
`couriers/` prefix so it never mixes with `menu/`. The public URL is then
`https://storage.googleapis.com/mm-product-images/couriers/{code}.png`, which is
exactly what migration 134 sets on `couriers.logo_url`.

GCS rather than R2: production serves its product images from GCS, and the R2
credentials on the box are not valid, so R2 is not a usable target.

A thin wrapper over `gcloud storage cp`, so it runs wherever `gcloud` is
authenticated to the project (a maintainer's machine, or the CI/VM service
account) — no extra Python dependency. It is a deliberate one-time write against
object storage; the logos change about never, and a re-run simply overwrites.

    python -m scripts.upload_courier_logos            # upload all
    python -m scripts.upload_courier_logos --dry-run  # print what it would do

To replace a badge with a real trademarked logo later: drop a new 256x256 PNG in
over `scripts/courier_logos/{code}.png` and re-run. Nothing else changes —
`couriers.logo_url` already points at this key, and every app reads that.
"""

from __future__ import annotations

import argparse
import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOGO_DIR = os.path.join(_HERE, "courier_logos")
_BUCKET = "gs://mm-product-images/couriers"
_PUBLIC = "https://storage.googleapis.com/mm-product-images/couriers"


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload courier badges to GCS.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the objects that would be written, and do nothing.",
    )
    args = parser.parse_args()

    files = sorted(f for f in os.listdir(_LOGO_DIR) if f.endswith(".png"))
    if not files:
        raise SystemExit(f"No PNGs found in {_LOGO_DIR}")

    if args.dry_run:
        for name in files:
            print(f"would upload {name} -> {_BUCKET}/{name}  ({_PUBLIC}/{name})")
        return

    # One `cp` for the whole directory — gcloud parallelises it. Content-type is
    # inferred from the .png extension; the bucket is uniformly public-read, so
    # no per-object ACL is set.
    src = os.path.join(_LOGO_DIR, "*.png")
    try:
        subprocess.run(
            f"gcloud storage cp {src} {_BUCKET}/",
            shell=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"gcloud storage cp failed: {exc}")
    for name in files:
        print(f"uploaded {name}  ({_PUBLIC}/{name})")
    print(f"done — {len(files)} courier logos in {_PUBLIC}/")


if __name__ == "__main__":
    main()
