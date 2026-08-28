from __future__ import annotations

import uuid
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel

from app.core import object_storage
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


@router.post(
    "/image", response_model=UploadResponse, status_code=status.HTTP_201_CREATED
)
async def upload_image(
    file: UploadFile = File(...),
    folder: str = Query(
        "products", description="GCS folder prefix (e.g. products, categories)"
    ),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Upload an image to Google Cloud Storage (admin only). Returns public URL."""
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
    # a PNG that came back out as JPEG must not be stored under `.png`, or GCS
    # serves JPEG bytes as `image/png`.
    ext = extension_for(content_type)
    key = f"{folder}/{uuid.uuid4()}{ext}"

    try:
        object_storage.upload_object(
            bucket=settings.GCS_IMAGE_BUCKET,
            key=key,
            body=contents,
            content_type=content_type,
            cache_control="public, max-age=31536000",
        )
    except Exception as e:
        raise BadGatewayError(f"Failed to upload image: {str(e)}")

    public_url = object_storage.public_url(settings.GCS_IMAGE_BUCKET, key)

    # Build the storefront's derivatives now, while nobody is waiting on them.
    # Whoever uploaded this will see it immediately because their own page view
    # warms it; the customer who lands on it tomorrow is the one who would
    # otherwise pay for the encode.
    image_warm_service.warm_in_background([public_url])

    return UploadResponse(url=public_url, key=key)


@router.delete("/image", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    key: str = Query(..., description="GCS object key or full public URL"),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Delete an image from Google Cloud Storage by key or URL (admin only)."""
    # Accept either a full URL or a raw key
    if key.startswith("http"):
        parsed = urlparse(key)
        object_key = parsed.path.lstrip("/")
        # A full public URL is /{bucket}/{key}; strip the leading bucket segment.
        prefix = f"{settings.GCS_IMAGE_BUCKET}/"
        if object_key.startswith(prefix):
            object_key = object_key[len(prefix) :]
    else:
        object_key = key

    try:
        object_storage.delete_object(bucket=settings.GCS_IMAGE_BUCKET, key=object_key)
    except Exception as e:
        raise BadGatewayError(f"Failed to delete image: {str(e)}")
