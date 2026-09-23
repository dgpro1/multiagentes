"""Public hosting for template header samples.

Template media headers are reviewed against a sample file, which is hosted
here at a public HTTPS URL. URLs carry no signature: the handle is
unguessable and the bytes served carry nosniff so they can never run as
pages. Outbound message attachments travel through the provider upload
instead and need no hosting.
"""

import secrets
from pathlib import Path

from fastapi import HTTPException

from ..config import get_settings
from .attachments import ensure_uploadable

SAMPLE_DIR = "messaging_samples"
SAMPLE_MAX_BYTES = 16 * 1024 * 1024


def sample_path(handle: str) -> Path:
    return get_settings().storage_dir / SAMPLE_DIR / f"{handle}.bin"


def store_sample(data: bytes, mime: str, filename: str) -> str:
    """Persist a template header sample and return its handle."""
    ensure_uploadable(mime)
    if not data or len(data) > SAMPLE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="The sample is empty or exceeds the 16 MB limit.")
    handle = secrets.token_hex(16)
    directory = get_settings().storage_dir / SAMPLE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{handle}.bin"
    path.write_bytes(data)
    (directory / f"{handle}.mime").write_text(mime, encoding="utf-8")
    safe = "".join(char for char in (filename or "sample") if char.isalnum() or char in ".-_ ")[:120] or "sample"
    (directory / f"{handle}.name").write_text(safe, encoding="utf-8")
    return handle


def sample_url(handle: str) -> str:
    from . import messaging_provider as provider

    base = provider.public_base()
    if not base.startswith("https://"):
        raise HTTPException(status_code=409, detail="A public HTTPS address is required to upload header samples.")
    return f"{base}/api/public/messaging/samples/{handle}"


def read_sample(handle: str) -> tuple[bytes, str, str]:
    directory = get_settings().storage_dir / SAMPLE_DIR
    try:
        data = (directory / f"{handle}.bin").read_bytes()
        mime = (directory / f"{handle}.mime").read_text(encoding="utf-8")
        name = (directory / f"{handle}.name").read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Sample not found.") from exc
    if not data:
        raise HTTPException(status_code=404, detail="Sample not found.")
    return data, mime, name
