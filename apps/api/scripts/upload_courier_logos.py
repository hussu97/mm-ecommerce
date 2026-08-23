"""Upload the courier badge PNGs to the images bucket, once.

The badges in `scripts/courier_logos/*.png` are uniform 256x256 brand marks —
one per courier code (`talabat`, `noon_food`, `noon_send`, `lalamove`, …). This
puts each at `couriers/{code}.png` in the same Cloudflare R2 images bucket the
storefront's product images live in, but under its own `couriers/` prefix so it
never mixes with `products/`. The public URL is then
`{CLOUDFLARE_R2_PUBLIC_URL}/couriers/{code}.png`, which is exactly what
migration 133 seeds onto `couriers.logo_url`.

A script, not something the app does on start-up: it is a deliberate one-time
write against object storage, and the logos change about never. Idempotent — a
re-run simply overwrites with the same bytes, so it is safe to run after every
logo refresh.

Run from apps/api, with the R2 credentials in the environment (the same four
`CLOUDFLARE_R2_*` the upload endpoint uses):

    python -m scripts.upload_courier_logos            # upload all
    python -m scripts.upload_courier_logos --dry-run  # list what it would do

To replace a badge with a real trademarked logo later: drop a new 256x256 PNG
in over `scripts/courier_logos/{code}.png` and re-run. Nothing else changes —
`couriers.logo_url` already points at this key, and every app reads that.
"""

from __future__ import annotations

import argparse
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOGO_DIR = os.path.join(_HERE, "courier_logos")
_PREFIX = "couriers"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT,
        aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY,
        aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_KEY,
        region_name="auto",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload courier badges to R2.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the objects that would be written, and do nothing.",
    )
    args = parser.parse_args()

    files = sorted(f for f in os.listdir(_LOGO_DIR) if f.endswith(".png"))
    if not files:
        raise SystemExit(f"No PNGs found in {_LOGO_DIR}")

    base = (settings.CLOUDFLARE_R2_PUBLIC_URL or "").rstrip("/")
    if args.dry_run:
        for name in files:
            print(f"would upload {name} -> {_PREFIX}/{name}  ({base}/{_PREFIX}/{name})")
        return

    if not (
        settings.CLOUDFLARE_R2_ENDPOINT
        and settings.CLOUDFLARE_R2_ACCESS_KEY
        and settings.CLOUDFLARE_R2_BUCKET
    ):
        raise SystemExit(
            "CLOUDFLARE_R2_* settings are not configured — run this where the "
            "R2 credentials are set (the production VM or a shell with the .env)."
        )

    client = _client()
    for name in files:
        key = f"{_PREFIX}/{name}"
        with open(os.path.join(_LOGO_DIR, name), "rb") as fh:
            body = fh.read()
        try:
            client.put_object(
                Bucket=settings.CLOUDFLARE_R2_BUCKET,
                Key=key,
                Body=body,
                ContentType="image/png",
                CacheControl="public, max-age=31536000",
            )
        except (BotoCoreError, ClientError) as exc:
            raise SystemExit(f"Failed to upload {key}: {exc}")
        print(f"uploaded {key}  ({base}/{key})")

    print(f"done — {len(files)} courier logos in {_PREFIX}/")


if __name__ == "__main__":
    main()
