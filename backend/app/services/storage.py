"""Shared media/object byte reads for both storage backends.

A single best-effort reader that opens a stored object by key on whichever
backend is active (``MEDIA_BACKEND=local`` or ``s3``) and **never raises** — a
missing broker logo must never fail a pack or a generated PDF. Historically this
lived in :mod:`app.services.packs`; it now lives here so both the pack builder
and the new HTML→PDF service (:mod:`app.services.pdf`) share one implementation.
``packs`` re-exports :func:`_media_bytes` for backward compatibility.
"""
from __future__ import annotations

import os

from app.core.config import settings

__all__ = ["_media_bytes"]


def _media_bytes(key: str | None) -> bytes | None:
    """Best-effort read of a media object (e.g. the broker logo).

    Returns ``None`` for a missing/unreadable object — never raises, so a
    missing logo degrades to Radal-only branding instead of a 500.
    """
    if not key:
        return None
    backend = (settings.MEDIA_BACKEND or "local").lower()
    try:
        if backend == "s3":
            import boto3  # noqa: PLC0415

            client = boto3.client("s3", region_name=settings.S3_REGION)
            obj = client.get_object(Bucket=settings.S3_BUCKET, Key=key)
            return obj["Body"].read()
        for candidate in (
            os.path.join(settings.MEDIA_LOCAL_DIR, key),
            os.path.join(
                settings.MEDIA_LOCAL_DIR,
                (settings.MEDIA_S3_PREFIX or "media/").strip("/"),
                key,
            ),
        ):
            if os.path.exists(candidate):
                with open(candidate, "rb") as fh:
                    return fh.read()
    except Exception:  # noqa: BLE001 - a missing logo must not fail a render
        return None
    return None
