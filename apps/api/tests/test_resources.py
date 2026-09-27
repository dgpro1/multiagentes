"""The resource library and the client's own R2 bucket.

boto3 is replaced by an in-memory bucket, so nothing here reaches Cloudflare.
"""

import io

import pytest
from botocore.exceptions import ClientError

from app.services import resource_storage

ACCOUNT = "0123456789abcdef0123456789abcdef"
CREDENTIALS = {
    "account_id": ACCOUNT,
    "access_key_id": "AKIAEXAMPLEKEY1234",
    "secret_access_key": "super-secret-value-never-shown",
    "bucket": "acme-library",
}


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803 - boto3's own names
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)
        self.deleted.append(Key)


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket()
    monkeypatch.setattr(resource_storage, "_build_client", lambda *args, **kwargs: fake)
    resource_storage._cache.clear()
    return fake


@pytest.fixture
def acme(authenticated_client):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()
    return client, customer["id"]


def connect(client, client_id):
    response = client.put(f"/api/clients/{client_id}/storage", json=CREDENTIALS)
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, client_id, name="Catalog", content=b"%PDF-1.4 data", mime="application/pdf", filename="catalog.pdf"):
    return client.post(
        f"/api/clients/{client_id}/resources/upload",
        data={"name": name, "description": "The price list"},
        files={"file": (filename, content, mime)},
    )


def test_a_client_starts_without_storage(acme):
    client, client_id = acme
    body = client.get(f"/api/clients/{client_id}/storage").json()
    assert body["status"] == "none"


def test_connecting_probes_the_bucket_and_never_returns_the_secret(acme, bucket):
    client, client_id = acme
    body = connect(client, client_id)
    assert body["status"] == "connected"
    assert body["bucket"] == "acme-library"
    assert body["access_key_hint"] == "••••1234"
    assert CREDENTIALS["secret_access_key"] not in str(body)
    assert CREDENTIALS["secret_access_key"] not in client.get(f"/api/clients/{client_id}/storage").text
    # The probe object is written, read back and removed.
    assert bucket.objects == {}
    assert any(key.startswith("_hunterai/probe-") for key in bucket.deleted)


def test_bad_credentials_are_refused_and_not_kept(acme, monkeypatch):
    client, client_id = acme

    class Refusing(FakeBucket):
        def put_object(self, **kwargs):
            raise resource_storage.StorageError("Cloudflare refused these credentials or the bucket permissions")

    monkeypatch.setattr(resource_storage, "_build_client", lambda *a, **k: Refusing())
    resource_storage._cache.clear()
    response = client.put(f"/api/clients/{client_id}/storage", json=CREDENTIALS)
    assert response.status_code == 422
    body = client.get(f"/api/clients/{client_id}/storage").json()
    assert body["status"] == "error"
    assert body["bucket"] == ""
    assert upload(client, client_id).status_code == 409


def test_the_endpoint_is_built_from_the_account_id_never_typed(acme, bucket):
    client, client_id = acme
    for bad in ("localhost", "169.254.169.254", "evil.example.com/x", "zzzz"):
        response = client.put(f"/api/clients/{client_id}/storage", json={**CREDENTIALS, "account_id": bad.ljust(32, "0")[:32]})
        assert response.status_code == 422
    assert resource_storage.endpoint_for(ACCOUNT) == f"https://{ACCOUNT}.r2.cloudflarestorage.com"


def test_uploading_needs_a_connected_bucket(acme):
    client, client_id = acme
    assert upload(client, client_id).status_code == 409


def test_a_file_is_uploaded_listed_downloaded_and_deleted(acme, bucket):
    client, client_id = acme
    connect(client, client_id)
    created = upload(client, client_id)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["kind"] == "file" and row["media_kind"] == "file" and row["size_bytes"] == 13
    assert len(bucket.objects) == 1
    key = next(iter(bucket.objects))
    assert client_id in key  # keys are opaque and namespaced by client

    assert [r["name"] for r in client.get(f"/api/clients/{client_id}/resources").json()] == ["Catalog"]
    fetched = client.get(f"/api/clients/{client_id}/resources/{row['id']}/file")
    assert fetched.status_code == 200 and fetched.content == b"%PDF-1.4 data"
    assert fetched.headers["content-type"] == "application/octet-stream"  # a document is never rendered inline

    assert client.delete(f"/api/clients/{client_id}/resources/{row['id']}").status_code == 204
    assert bucket.objects == {}


def test_upload_rules_by_kind_and_type(acme, bucket):
    client, client_id = acme
    connect(client, client_id)
    assert upload(client, client_id, "Logo", b"<svg/>", "image/svg+xml", "logo.svg").status_code == 400
    assert upload(client, client_id, "Page", b"<html/>", "text/html", "x.html").status_code == 400
    assert upload(client, client_id, "Empty", b"", "image/png", "e.png").status_code == 422
    # An image is capped at 5 MB (WhatsApp's own limit) even though the client allows 10.
    assert upload(client, client_id, "Big", b"x" * (5 * 1024 * 1024 + 1), "image/png", "b.png").status_code == 413
    ok = upload(client, client_id, "Photo", b"x" * 1024, "image/png", "p.png")
    assert ok.status_code == 201 and ok.json()["media_kind"] == "image"
    assert client.patch(f"/api/clients/{client_id}/storage/limits", json={"max_file_mb": 20}).status_code == 200
    # A video is capped at 16 MB whatever the client allows.
    assert upload(client, client_id, "Clip", b"x" * (16 * 1024 * 1024 + 1), "video/mp4", "c.mp4").status_code == 413


def test_the_quota_stops_uploads(acme, bucket):
    client, client_id = acme
    connect(client, client_id)
    client.patch(f"/api/clients/{client_id}/storage/limits", json={"quota_mb": 1})
    assert upload(client, client_id, "One", b"x" * 700_000, "application/pdf", "1.pdf").status_code == 201
    assert upload(client, client_id, "Two", b"x" * 700_000, "application/pdf", "2.pdf").status_code == 413


def test_links_and_names(acme):
    client, client_id = acme
    url = f"/api/clients/{client_id}/resources"
    made = client.post(url, json={"kind": "link", "name": "Book online", "url": "https://acme.test/book",
                                  "message_template": "Hi {{contact.name}}, book here: {{url}}"})
    assert made.status_code == 201, made.text
    # Names are unique per client whatever the case, and cannot break a [Recurso: ...] token.
    assert client.post(url, json={"kind": "link", "name": "book ONLINE", "url": "https://x.test"}).status_code == 409
    assert client.post(url, json={"kind": "link", "name": "Bad]", "url": "https://x.test"}).status_code == 422
    assert client.post(url, json={"kind": "link", "name": "NoUrl"}).status_code == 422
    assert client.post(url, json={"kind": "link", "name": "Js", "url": "javascript:alert(1)"}).status_code == 422
    renamed = client.patch(f"{url}/{made.json()['id']}", json={"name": "Reserve", "is_active": False})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Reserve" and renamed.json()["is_active"] is False


def test_disconnecting_forgets_the_credentials_but_touches_no_file(acme, bucket):
    client, client_id = acme
    connect(client, client_id)
    upload(client, client_id)
    stored = dict(bucket.objects)
    body = client.delete(f"/api/clients/{client_id}/storage").json()
    assert body["status"] == "pending" and body["bucket"] == ""
    assert bucket.objects == stored  # the files are the customer's
    row = client.get(f"/api/clients/{client_id}/resources").json()[0]
    assert client.get(f"/api/clients/{client_id}/resources/{row['id']}/file").status_code == 409


def test_the_public_link_connects_without_a_session(acme, bucket):
    client, client_id = acme
    link = client.post(f"/api/clients/{client_id}/storage/link").json()
    token = link["connect_url"].rsplit("/", 1)[1]
    assert "/connect/storage/" in link["connect_url"]

    anonymous = type(client)(client.app)
    info = anonymous.get(f"/api/storage/connect/{token}")
    assert info.status_code == 200 and info.json()["client_name"] == "Acme" and info.json()["status"] == "pending"
    done = anonymous.post(f"/api/storage/connect/{token}", json=CREDENTIALS)
    assert done.status_code == 200 and done.json()["status"] == "connected"
    assert client.get(f"/api/clients/{client_id}/storage").json()["status"] == "connected"
    assert anonymous.get("/api/storage/connect/not-a-real-token").status_code == 404

    # Renewing the link kills the old one.
    client.post(f"/api/clients/{client_id}/storage/link")
    assert anonymous.get(f"/api/storage/connect/{token}").status_code == 404


def test_the_portal_library_is_gated_by_its_function_and_permission(authenticated_client, bucket):
    from fastapi.testclient import TestClient

    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Portal Co", "is_active": True}).json()
    slug = customer["portal_slug"]
    admin = {"name": "Ana", "email": f"ana@{slug}.com", "password": "secure-portal"}
    agent = {"name": "Beto", "email": f"beto@{slug}.com", "password": "secure-portal", "role": "agent"}
    for body in (admin, agent):
        assert client.post(f"/api/clients/{customer['id']}/portal-users", json=body).status_code == 201
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    owner, operator = TestClient(client.app), TestClient(client.app)
    assert owner.post(f"/api/portal/{slug}/login", json={"email": admin["email"], "password": "secure-portal"}).status_code == 200
    assert operator.post(f"/api/portal/{slug}/login", json={"email": agent["email"], "password": "secure-portal"}).status_code == 200
    base = f"/api/portal/{slug}"

    # Off by default: the whole library answers 403.
    assert owner.get(f"{base}/resources").status_code == 403
    assert owner.get(f"{base}/storage").status_code == 403
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_features": {"resources": True}})

    # The portal admin connects the bucket and uploads; the secret never comes back.
    connected = owner.put(f"{base}/storage", json=CREDENTIALS)
    assert connected.status_code == 200 and connected.json()["status"] == "connected"
    assert CREDENTIALS["secret_access_key"] not in connected.text
    made = owner.post(f"{base}/resources/upload", data={"name": "Menu"}, files={"file": ("menu.pdf", b"%PDF", "application/pdf")})
    assert made.status_code == 201, made.text
    rid = made.json()["id"]
    assert owner.get(f"{base}/resources/{rid}/file").content == b"%PDF"

    # An operator without the permission reads but changes nothing.
    assert [r["name"] for r in operator.get(f"{base}/resources").json()] == ["Menu"]
    assert operator.post(f"{base}/resources", json={"kind": "link", "name": "X", "url": "https://x.test"}).status_code == 403
    assert operator.delete(f"{base}/resources/{rid}").status_code == 403
    assert operator.delete(f"{base}/storage").status_code == 403

    assert owner.delete(f"{base}/resources/{rid}").status_code == 204


def test_the_v1_api_reads_and_writes_the_library_by_scope(acme, bucket):
    client, client_id = acme
    connect(client, client_id)

    def token(preset):
        integration = client.post("/api/integrations", json={"name": f"Key {preset}", "preset": preset}).json()
        issued = client.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()
        return {"Authorization": f"Bearer {issued['token']}"}

    reader, writer = token("read_only"), token("full")
    base = f"/api/v1/clients/{client_id}"
    link = {"kind": "link", "name": "Book", "url": "https://acme.test/book"}

    refused = client.post(f"{base}/resources", json=link, headers=reader)
    assert refused.status_code == 403 and refused.json()["status"] == 403

    first = client.post(f"{base}/resources", json=link, headers={**writer, "Idempotency-Key": "k-1"})
    again = client.post(f"{base}/resources", json=link, headers={**writer, "Idempotency-Key": "k-1"})
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["id"] == again.json()["id"]

    upload = client.post(f"{base}/resources/upload", headers=writer,
                         data={"name": "Menu"}, files={"file": ("menu.pdf", b"%PDF", "application/pdf")})
    assert upload.status_code == 201, upload.text
    assert upload.json()["_links"]["file"].endswith("/file")

    listed = client.get(f"{base}/resources", headers=reader).json()
    assert {row["name"] for row in listed["data"]} == {"Book", "Menu"}
    state = client.get(f"{base}/storage", headers=reader).json()
    assert state["status"] == "connected" and "access_key_hint" not in state
    assert CREDENTIALS["secret_access_key"] not in str(state)


def test_attachments_move_to_the_clients_bucket_and_are_still_served(acme, bucket):
    from datetime import timedelta
    import uuid as _uuid

    from conftest import TestingSession, customer_conversation
    from app.models import Message, MessageAttachment
    from app.services.attachment_offload import offload_batch

    client, client_id = acme
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": client_id, "provider": "openrouter", "model": "openai/gpt-5.6-luna",
        "name": "Vera", "instructions": "", "personality": "", "is_active": True}).json()
    conversation = customer_conversation(client, agent["id"])

    with TestingSession() as db:
        message = Message(conversation_id=_uuid.UUID(conversation["id"]), role="user", content="", sender_type="visitor")
        db.add(message)
        db.flush()
        attachment = MessageAttachment(message_id=message.id, kind="image", mime="image/png", filename="p.png",
                                       size_bytes=4, data=b"\x89PNG")
        db.add(attachment)
        db.commit()
        attachment_id = attachment.id

    url = f"/api/conversations/{conversation['id']}/attachments/{attachment_id}"
    # Without a bucket nothing moves.
    with TestingSession() as db:
        assert offload_batch(db, grace=timedelta(0)) == 0

    connect(client, client_id)
    with TestingSession() as db:
        assert offload_batch(db, grace=timedelta(0)) == 1
        moved = db.get(MessageAttachment, attachment_id)
        assert moved.storage_key and moved.data is None
    assert bucket.objects[moved.storage_key] == b"\x89PNG"
    served = client.get(url)
    assert served.status_code == 200 and served.content == b"\x89PNG"

    # Disconnected, the moved file is refused rather than served empty.
    client.delete(f"/api/clients/{client_id}/storage")
    assert client.get(url).status_code == 409


def test_a_large_photo_is_fitted_to_1600px_on_upload(acme, bucket):
    from PIL import Image

    client, client_id = acme
    connect(client, client_id)
    raw = io.BytesIO()
    Image.effect_noise((3200, 2400), 90).convert("RGB").save(raw, "JPEG", quality=95)
    photo = raw.getvalue()
    made = upload(client, client_id, "Storefront", photo, "image/jpeg", "front.jpg")
    assert made.status_code == 201, made.text
    stored = bucket.objects[next(iter(bucket.objects))]
    assert made.json()["size_bytes"] == len(stored) < len(photo)
    assert max(Image.open(io.BytesIO(stored)).size) == 1600


def test_a_visitor_file_over_its_kind_limit_keeps_only_a_notice():
    from app.models import Message
    from app.services.attachments import store_visitor_attachment

    message = Message(content="mira esto", llm_content="mira esto")
    assert store_visitor_attachment(None, message, data=b"x" * (5 * 1024 * 1024 + 1), mime="image/jpeg", filename="big.jpg") is None
    assert "[Archivo demasiado grande para guardarse: big.jpg, 5.0 MB]" in message.content
    assert message.llm_content.endswith("5.0 MB]")
