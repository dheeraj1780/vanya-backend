"""
Stores plant photos. STORAGE_BACKEND=local (the default) writes to disk and
serves the file via this API's own static mount — fully functional for
development and small-scale use, not a mock. Swap to S3/Supabase Storage
before a real launch by implementing the `_save_to_s3` branch below; the
function signature callers use (`save_plant_photo`) does not need to change.
"""
import base64
import os
import uuid

import aiofiles

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, InternalServerError, PayloadTooLargeError

settings = get_settings()

_MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5MB — generous for a client-compressed ~1024px JPEG


async def save_plant_photo(image_base64: str, plant_id: str) -> str:
    """Decodes and saves a base64 image, returning its publicly accessible URL."""
    try:
        try:
            raw_bytes = base64.b64decode(image_base64, validate=True)
        except Exception as exc:
            raise BadRequestError(f"image_base64 is not valid base64: {exc}") from exc

        if len(raw_bytes) > _MAX_PHOTO_BYTES:
            raise PayloadTooLargeError("Image exceeds the maximum allowed size (5MB)")

        if settings.storage_backend == "local":
            return await _save_to_local_disk(raw_bytes, plant_id)

        # Production swap point — implement using boto3 / Supabase Storage client,
        # keeping the same return type (a public URL string).
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
