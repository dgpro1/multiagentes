"""Header samples are hosted at a public URL the template refers to."""

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.services import messaging_media as media
from app.services import whatsapp_templates as templates


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_a_sample_becomes_a_public_url(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    monkeypatch.setattr(get_settings(), "messaging_provider_public_url", "https://files.example.test")
    url = await templates.upload_sample("acct-1", data=b"\x89PNG", mime="image/png", filename="promo.png")
    assert url.startswith("https://files.example.test/api/public/messaging/samples/")
    handle = url.rsplit("/", 1)[-1]
    data, mime, name = media.read_sample(handle)
    assert (data, mime, name) == (b"\x89PNG", "image/png", "promo.png")


@pytest.mark.anyio
async def test_samples_need_a_public_https_origin(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    monkeypatch.setattr(get_settings(), "messaging_provider_public_url", "")
    monkeypatch.setattr(get_settings(), "social_public_url", "")
    monkeypatch.setattr(get_settings(), "frontend_url", "http://localhost:3000")
    with pytest.raises(HTTPException) as caught:
        await templates.upload_sample("acct-1", data=b"x", mime="image/png", filename="a.png")
    assert caught.value.status_code == 409


@pytest.mark.anyio
async def test_samples_are_checked_before_leaving():
    class Upload:
        def __init__(self, content_type, filename, data):
            self.content_type, self.filename, self._data = content_type, filename, data

        async def read(self, n):
            return self._data[:n]

    data, mime, name = await templates.read_sample(Upload("image/jpg", "../a b.jpg", b"jpg"))
    assert (data, mime, name) == (b"jpg", "image/jpeg", "a b.jpg")
    with pytest.raises(HTTPException) as caught:
        await templates.read_sample(Upload("image/gif", "a.gif", b"gif"))
    assert caught.value.status_code == 415
    with pytest.raises(HTTPException) as caught:
        await templates.read_sample(Upload("application/pdf", "big.pdf", b"x" * (media.SAMPLE_MAX_BYTES + 1)))
    assert caught.value.status_code == 413
