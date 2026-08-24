from __future__ import annotations

import uuid
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import BadGatewayError, BadRequestError
from app.core.images import extension_for, optimize_image
from app.core.permissions import require
from app.models.user import User
from app.services import image_warm_service

router = APIRouter()

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


class UploadResponse(BaseModel):
    url: str
    key: str


def _get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT,
        aws_access_key_id=settings.CLOUDFLARE_R2_ACCESS_KEY,
        aws_secret_access_key=settings.CLOUDFLARE_R2_SECRET_KEY,
        region_name="auto",
    )


@router.post(
    "/image", response_model=UploadResponse, status_code=status.HTTP_201_CREATED
)
async def upload_image(
    file: UploadFile = File(...),
    folder: str = Query(
        "products", description="R2 folder prefix (e.g. products, categories)"
    ),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Upload an image to Cloudflare R2 (admin only). Returns public URL."""
    # Validate content type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise BadRequestError(
            f"Invalid file type '{content_type}'. Allowed: jpeg, png, webp"
        )

    # Read and validate size
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise BadRequestError(
            f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB"
        )

    # Downscale and re-encode before it ever reaches the bucket. A camera-roll
    # photo is several thousand pixels wide and the storefront never renders one
    # above ~960; storing the original just makes every cold image transform
    # slower for the first visitor who lands on that product.
    contents, content_type = optimize_image(contents, content_type)

    # Generate unique key. The extension has to follow the *re-encoded* type —
    # a PNG that came back out as JPEG must not be stored under `.png`, or R2
    # serves JPEG bytes as `image/png`.
    ext = extension_for(content_type)
    key = f"{folder}/{uuid.uuid4()}{ext}"

    try:
        client = _get_r2_client()
        client.put_object(
            Bucket=settings.CLOUDFLARE_R2_BUCKET,
            Key=key,
            Body=contents,
            ContentType=content_type,
            CacheControl="public, max-age=31536000",
        )
    except (BotoCoreError, ClientError) as e:
        raise BadGatewayError(f"Failed to upload image: {str(e)}")

    public_url = f"{settings.CLOUDFLARE_R2_PUBLIC_URL.rstrip('/')}/{key}"

    # Build the storefront's derivatives now, while nobody is waiting on them.
    # Whoever uploaded this will see it immediately because their own page view
    # warms it; the customer who lands on it tomorrow is the one who would
    # otherwise pay for the encode.
    image_warm_service.warm_in_background([public_url])

    return UploadResponse(url=public_url, key=key)


@router.delete("/image", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    key: str = Query(..., description="R2 object key or full public URL"),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Delete an image from Cloudflare R2 by key or URL (admin only)."""
    # Accept either a full URL or a raw key
    if key.startswith("http"):
        parsed = urlparse(key)
        object_key = parsed.path.lstrip("/")
    else:
        object_key = key

    try:
        client = _get_r2_client()
        client.delete_object(Bucket=settings.CLOUDFLARE_R2_BUCKET, Key=object_key)
    except (BotoCoreError, ClientError) as e:
        raise BadGatewayError(f"Failed to delete image: {str(e)}")
