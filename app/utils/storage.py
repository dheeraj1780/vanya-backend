"""
Stores plant photos. STORAGE_BACKEND=local (the default) writes to disk and
serves the file via this API's own static mount — fine for local dev, but
NOT safe in production: most hosting platforms (Render, Railway, etc.)
give a container an ephemeral filesystem that's wiped on every redeploy or
restart, silently deleting every user's plant photos. STORAGE_BACKEND=s3
writes to any S3-compatible bucket instead (Cloudflare R2, AWS S3,
Backblaze B2, DigitalOcean Spaces — same API, just a different
endpoint_url) and survives redeploys the way a real production app needs
to. The function signature callers use (`save_plant_photo`) is identical
either way.
"""
import asyncio
import base64
import os
import uuid
from typing import Optional

import aiofiles
import boto3
from botocore.config import Config as BotoConfig

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, InternalServerError, PayloadTooLargeError

settings = get_settings()

_MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5MB — generous for a client-compressed ~1024px JPEG

_s3_client = None  # lazily constructed — only ever needed when STORAGE_BACKEND=s3


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _s3_client


async def save_plant_photo(image_base64: str, plant_id: str) -> str:
    """Decodes and saves a base64 image, returning its publicly accessible URL."""
    try:
        try:
            raw_bytes = base64.b64decode(image_base64, validate=True)
        except Exception as exc:
            raise BadRequestError(f"image_base64 is not valid base64: {exc}") from exc

        if len(raw_bytes) > _MAX_PHOTO_BYTES:
            raise PayloadTooLargeError("Image exceeds the maximum allowed size (5MB)")

        if settings.storage_backend == "s3":
            return await _save_to_s3(raw_bytes, plant_id)
        if settings.storage_backend == "local":
            return await _save_to_local_disk(raw_bytes, plant_id)

        raise InternalServerError(f"Unsupported STORAGE_BACKEND: {settings.storage_backend}")
    except (BadRequestError, PayloadTooLargeError, InternalServerError):
        raise
    except Exception as exc:
        raise InternalServerError(f"Failed to save plant photo: {exc}") from exc


async def _save_to_local_disk(raw_bytes: bytes, plant_id: str) -> str:
    os.makedirs(settings.local_storage_dir, exist_ok=True)
    filename = f"{plant_id}-{uuid.uuid4().hex[:8]}.jpg"
    filepath = os.path.join(settings.local_storage_dir, filename)
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(raw_bytes)
    return f"{settings.public_base_url}/uploads/{filename}"


async def delete_plant_photo(photo_url: Optional[str]) -> None:
    """Best-effort cleanup of the object/file a photo_url points at — called
    when a plant is deleted, and when a photo is replaced by a newer one.
    Deliberately never raises: losing the DB row (or landing the new photo)
    is the operation that actually matters to the caller, and a storage
    provider hiccup here shouldn't turn that into a failed request. Without
    this, every deleted/replaced plant photo silently orphans its object in
    the bucket forever (harmless short-term, but an unbounded storage leak
    over the app's lifetime)."""
    if not photo_url:
        return
    try:
        if settings.storage_backend == "s3":
            base = settings.s3_public_url_base.rstrip("/")
            if not photo_url.startswith(base + "/"):
                return  # not one of ours (e.g. left over from a past backend) — nothing to clean up
            key = photo_url[len(base) + 1:]

            def _delete():
                _get_s3_client().delete_object(Bucket=settings.s3_bucket, Key=key)

            await asyncio.to_thread(_delete)
        elif settings.storage_backend == "local":
            prefix = f"{settings.public_base_url}/uploads/"
            if not photo_url.startswith(prefix):
                return
            filename = photo_url[len(prefix):]
            filepath = os.path.join(settings.local_storage_dir, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
    except Exception:
        # Best-effort only — see docstring. Nothing to recover here; the
        # object is orphaned but the caller's actual request still succeeds.
        pass


async def _save_to_s3(raw_bytes: bytes, plant_id: str) -> str:
    key = f"plants/{plant_id}-{uuid.uuid4().hex[:8]}.jpg"

    def _put():
        _get_s3_client().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=raw_bytes,
            ContentType="image/jpeg",
        )

    try:
        # boto3 is synchronous/blocking — run it off the event loop so one
        # slow upload doesn't stall every other concurrent request this
        # async server is handling.
        await asyncio.to_thread(_put)
    except Exception as exc:
        raise InternalServerError(f"Failed to upload photo to S3-compatible storage: {exc}") from exc

    base = settings.s3_public_url_base.rstrip("/")
    return f"{base}/{key}"
