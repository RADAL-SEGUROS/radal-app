"""Media service — profile pictures / logos as WEBP 512x512.

Both ORGANIZATIONS (corredora, aseguradora_entidad) and HUMANS (usuario) share
one small avatar shape: WEBP, 512x512, center-cropped. On upload the raw bytes
are converted+resized with Pillow and persisted to a backend chosen by env:

- ``MEDIA_BACKEND=local`` -> writes under ``MEDIA_LOCAL_DIR`` and serves at
  ``MEDIA_PUBLIC_BASE_URL/<key>`` (good for local dev).
- ``MEDIA_BACKEND=s3``    -> puts to ``MEDIA_S3_BUCKET`` under ``MEDIA_S3_PREFIX``.

Pillow / boto3 are imported LAZILY inside the functions so this module (and the
whole app) keeps importing even when those optional deps are not installed; a
clear error is raised only when an upload is actually attempted.

The caller stores the returned ``(key, url)`` on the profile's ``foto_key`` /
``foto_url`` (or ``logo_url`` for the corredora logo).
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass

from app.core.config import settings

__all__ = [
    "MediaError",
    "StoredMedia",
    "AVATAR_SIZE",
    "process_and_store_avatar",
    "build_key",
]

AVATAR_SIZE = 512  # px, square
_WEBP_QUALITY = 82
_ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
}


class MediaError(Exception):
    """Raised on any media processing / storage failure (mapped to HTTP 4xx/5xx)."""


@dataclass(frozen=True)
class StoredMedia:
    key: str
    url: str


def build_key(entity_type: str, entity_id: int | str) -> str:
    """Deterministic-prefix, random-suffix media key (avoids cache collisions)."""
    slug = str(entity_id)
    token = uuid.uuid4().hex[:12]
    return f"{entity_type}/{slug}/avatar-{token}.webp"


def _to_webp_square(raw: bytes) -> bytes:
    """Convert raw image bytes to a 512x512 center-cropped WEBP."""
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415  (lazy optional dep)
    except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
        raise MediaError(
            "Procesamiento de imágenes no disponible: falta Pillow. "
            "Instale 'Pillow' para habilitar la subida de fotos/logos."
        ) from exc

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)  # honor camera orientation
        img = img.convert("RGB")
        # Center-crop to square, then resize to AVATAR_SIZE.
        img = ImageOps.fit(
            img, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS
        )
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=_WEBP_QUALITY, method=6)
        return out.getvalue()
    except MediaError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MediaError(f"No se pudo procesar la imagen: {exc}") from exc


def _store_local(key: str, data: bytes) -> str:
    import os

    base = settings.MEDIA_LOCAL_DIR
    path = os.path.join(base, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    base_url = settings.MEDIA_PUBLIC_BASE_URL.rstrip("/")
    return f"{base_url}/{key}"


def _store_s3(key: str, data: bytes) -> str:
    try:
        import boto3  # noqa: PLC0415  (lazy optional dep)
    except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
        raise MediaError(
            "Almacenamiento S3 no disponible: falta boto3. "
            "Instale 'boto3' o use MEDIA_BACKEND=local."
        ) from exc

    bucket = settings.MEDIA_S3_BUCKET
    full_key = f"{settings.MEDIA_S3_PREFIX.rstrip('/')}/{key}"
    try:
        client = boto3.client("s3", region_name=settings.MEDIA_S3_REGION)
        client.put_object(
            Bucket=bucket,
            Key=full_key,
            Body=data,
            ContentType="image/webp",
            CacheControl="public, max-age=31536000, immutable",
        )
    except Exception as exc:  # noqa: BLE001
        raise MediaError(f"No se pudo subir a S3: {exc}") from exc
    return f"https://{bucket}.s3.{settings.MEDIA_S3_REGION}.amazonaws.com/{full_key}"


def process_and_store_avatar(
    raw: bytes,
    *,
    entity_type: str,
    entity_id: int | str,
    content_type: str | None = None,
) -> StoredMedia:
    """Validate, convert to WEBP 512x512, store, and return ``(key, url)``.

    Args:
        raw: the uploaded file bytes.
        entity_type: logical owner ("broker" | "user" | "insured" |
            "insurer"), used only for the storage key prefix.
        entity_id: owner id, used in the storage key.
        content_type: optional declared MIME (a soft check; Pillow is the real
            gate).
    """
    if not raw:
        raise MediaError("Archivo vacío")

    max_bytes = settings.MEDIA_MAX_UPLOAD_MB * 1024 * 1024
    if len(raw) > max_bytes:
        raise MediaError(
            f"Archivo demasiado grande (máx {settings.MEDIA_MAX_UPLOAD_MB} MB)"
        )

    if content_type and content_type.lower() not in _ALLOWED_CONTENT_TYPES:
        raise MediaError(f"Tipo de archivo no soportado: {content_type}")

    webp = _to_webp_square(raw)
    key = build_key(entity_type, entity_id)

    backend = (settings.MEDIA_BACKEND or "local").lower()
    if backend == "s3":
        url = _store_s3(key, webp)
    else:
        url = _store_local(key, webp)

    return StoredMedia(key=key, url=url)
