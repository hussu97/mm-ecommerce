from __future__ import annotations

import mimetypes
import uuid
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import get_admin_user
from app.models.user import User

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
    _admin: User = Depends(get_admin_user),
):
    """Upload an image to Cloudflare R2 (admin only). Returns public URL."""
    # Validate content type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type '{content_type}'. Allowed: jpeg, png, webp",
        )

    # Read and validate size
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

    # Generate unique key
    ext = mimetypes.guess_extension(content_type) or ".jpg"
    if ext == ".jpe":
        ext = ".jpg"
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to upload image: {str(e)}",
        )

    public_url = f"{settings.CLOUDFLARE_R2_PUBLIC_URL.rstrip('/')}/{key}"
    return UploadResponse(url=public_url, key=key)


@router.delete("/image", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    key: str = Query(..., description="R2 object key or full public URL"),
    _admin: User = Depends(get_admin_user),
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to delete image: {str(e)}",
        )
