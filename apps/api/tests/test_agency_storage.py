"""The agency's own R2 bucket: connecting it, and moving a client's files in and out.

boto3 is replaced by in-memory buckets, one per access key, so a client's own
bucket and the agency's are different places and nothing reaches Cloudflare.
"""

import io
import uuid

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app import agency_features
from app.models import Agency, ClientStorageConnection, Message, MessageAttachment
from app.services import resource_storage
from conftest import TestingSession
from sqlalchemy import select

ACCOUNT = "0123456789abcdef0123456789abcdef"
OWN = {"account_id": ACCOUNT, "access_key_id": "OWNKEY-1234567890", "secret_access_key": "own-secret-never-shown", "bucket": "acme-own"}
AGENCY = {"account_id": ACCOUNT, "access_key_id": "AGENCYKEY-1234567890", "secret_access_key": "agency-secret-never-shown", "bucket": "agency-files"}
REFUSED = {**AGENCY, "access_key_id": "REFUSED-1234567890"}


class FakeS3:
    def __init__(self, refuse: bool = False):
        self.objects: dict[str, bytes] = {}
        self.refuse = refuse
        self.fail_puts = False

    def _check(self):
        if self.refuse:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "Call")

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803 - boto3's own names
        self._check()
        if self.fail_puts:
            raise ClientError({"Error": {"Code": "InternalError"}}, "PutObject")
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        self._check()
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):  # noqa: N803
        self._check()
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)


@pytest.fixture
def buckets(monkeypatch):
    made: dict[str, FakeS3] = {}

    def build(account_id, access_key_id, secret, region):
        return made.setdefault(access_key_id, FakeS3(refuse=access_key_id == REFUSED["access_key_id"]))

    monkeypatch.setattr(resource_storage, "_build_client", build)
    resource_storage._cache.clear()
    return made


def _agency_id(client: TestClient) -> uuid.UUID:
    return uuid.UUID(client.get("/api/agency").json()["id"])


def _module(client: TestClient, on: bool) -> None:
    with TestingSession() as db:
        agency = db.get(Agency, _agency_id(client))
        agency.features = agency_features.merged(agency.features, {"agency_backend": on})
        db.commit()


def _acme(client: TestClient) -> str:
    return client.post("/api/clients", json={"name": "Acme", "is_active": True}).json()["id"]


def _upload(client: TestClient, client_id: str, name: str = "Catalog", content: bytes = b"%PDF-1.4 data"):
    return client.post(
        f"/api/clients/{client_id}/resources/upload",
        data={"name": name, "description": "The price list"},
        files={"file": ("catalog.pdf", content, "application/pdf")},
    )


def _switch(client: TestClient, client_id: str, target: str):
    return client.post(f"/api/clients/{client_id}/storage/switch", json={"target": target})


def _read(client: TestClient, client_id: str, resource: dict) -> bytes:
    response = client.get(f"/api/clients/{client_id}/resources/{resource['id']}/file")
    assert response.status_code == 200, response.text
    return response.content


# --- the agency's bucket ------------------------------------------------------------------


def test_the_agency_connects_its_bucket_and_no_secret_comes_back(authenticated_client, buckets):
    client = authenticated_client
    assert client.put("/api/agency/storage", json=AGENCY).status_code == 403  # the module ships off
    _module(client, True)
    assert client.get("/api/agency/storage").json()["status"] == "none"
    connected = client.put("/api/agency/storage", json=AGENCY)
    assert connected.status_code == 200, connected.text
    body = connected.json()
    assert (body["status"], body["bucket"], body["account_id"]) == ("connected", "agency-files", ACCOUNT)
    assert body["access_key_hint"].endswith("7890")
    assert AGENCY["secret_access_key"] not in connected.text and AGENCY["access_key_id"] not in connected.text
    assert client.post("/api/agency/storage/check").json()["status"] == "connected"
    assert client.delete("/api/agency/storage").json()["status"] == "pending"
    assert client.get("/api/agency/storage").json()["bucket"] == ""


def test_a_bucket_that_refuses_the_credentials_is_not_kept(authenticated_client, buckets):
    client = authenticated_client
    _module(client, True)
    refused = client.put("/api/agency/storage", json=REFUSED)
    assert refused.status_code == 422
    assert client.get("/api/agency/storage").json()["status"] == "error"
    assert client.put("/api/agency/storage", json={**AGENCY, "account_id": "short"}).status_code == 422


# --- moving files ----------------------------------------------------------------------------


def test_files_move_to_the_agency_bucket_and_back(authenticated_client, buckets):
    client = authenticated_client
    _module(client, True)
    client_id = _acme(client)
    assert client.put(f"/api/clients/{client_id}/storage", json=OWN).status_code == 200
    first = _upload(client, client_id, "First", b"first file").json()
    assert client.put("/api/agency/storage", json=AGENCY).status_code == 200
    own, agency = buckets[OWN["access_key_id"]], buckets[AGENCY["access_key_id"]]
    assert len(own.objects) == 1 and not agency.objects

    moved = _switch(client, client_id, "agency")
    assert moved.status_code == 200, moved.text
    assert moved.json()["hosted_by"] == "agency" and moved.json()["provider"] == "agency"
    # The same key now exists in the agency's bucket, and the file still reads.
    assert set(agency.objects) == set(own.objects)
    assert _read(client, client_id, first) == b"first file"
    # What is uploaded now goes to the agency's bucket, never to the client's.
    second = _upload(client, client_id, "Second", b"second file").json()
    assert len(agency.objects) == 2 and len(own.objects) == 1
    assert _read(client, client_id, second) == b"second file"
    # No credential of the agency leaks into what the client's screens read.
    shown = client.get(f"/api/clients/{client_id}/storage")
    assert AGENCY["secret_access_key"] not in shown.text and AGENCY["bucket"] not in shown.text

    back = _switch(client, client_id, "client")
    assert back.status_code == 200, back.text
    assert back.json()["hosted_by"] == "client"
    assert set(own.objects) == set(agency.objects)  # the file added in between came along
    assert _read(client, client_id, second) == b"second file"
    assert _switch(client, client_id, "client").status_code == 409  # already there


def test_attachments_of_the_conversations_move_with_the_library(authenticated_client, buckets):
    client = authenticated_client
    _module(client, True)
    client_id = _acme(client)
    agent = client.post("/api/agents", json={
        "client_id": client_id, "name": "Vera", "instructions": "", "personality": "", "model": "", "is_active": True,
    }).json()
    channel = client.put(f"/api/webchat/channels/{client_id}", json={
        "agent_id": agent["id"], "greeting": "Hola", "color": "#075985", "is_enabled": True,
    }).json()
    assert client.post(f"/api/widget/{channel['public_id']}/messages", json={"session_id": "s1", "content": "hola"}).status_code == 200
    assert client.put(f"/api/clients/{client_id}/storage", json=OWN).status_code == 200
    assert client.put("/api/agency/storage", json=AGENCY).status_code == 200
    own, agency = buckets[OWN["access_key_id"]], buckets[AGENCY["access_key_id"]]
    key = f"{uuid.uuid4()}/{client_id}/attachments/{uuid.uuid4()}"
    own.objects[key] = b"\x89PNG-bytes"
    with TestingSession() as db:
        message = db.scalar(select(Message).limit(1))
        db.add(MessageAttachment(message_id=message.id, kind="image", mime="image/png", size_bytes=10, storage_key=key))
        db.commit()

    assert _switch(client, client_id, "agency").status_code == 200
    assert agency.objects[key] == b"\x89PNG-bytes"


def test_a_failed_copy_leaves_the_files_where_they_were(authenticated_client, buckets):
    client = authenticated_client
    _module(client, True)
    client_id = _acme(client)
    client.put(f"/api/clients/{client_id}/storage", json=OWN)
    _upload(client, client_id)
    client.put("/api/agency/storage", json=AGENCY)
    buckets[AGENCY["access_key_id"]].fail_puts = True
    failed = _switch(client, client_id, "agency")
    assert failed.status_code == 502
    assert client.get(f"/api/clients/{client_id}/storage").json()["hosted_by"] == "client"
    buckets[AGENCY["access_key_id"]].fail_puts = False
    assert _switch(client, client_id, "agency").status_code == 200


def test_a_client_with_no_bucket_of_its_own_can_be_hosted_but_not_sent_back_empty_handed(authenticated_client, buckets):
    client = authenticated_client
    _module(client, True)
    client_id = _acme(client)
    client.put("/api/agency/storage", json=AGENCY)
    assert _switch(client, client_id, "agency").status_code == 200
    stored = _upload(client, client_id, "Menu", b"menu bytes")
    assert stored.status_code in (200, 201), stored.text
    assert len(buckets[AGENCY["access_key_id"]].objects) == 1
    # Going back needs a bucket of its own to receive the files.
    assert _switch(client, client_id, "client").status_code == 409
    assert client.put(f"/api/clients/{client_id}/storage", json=OWN).status_code == 200
    assert client.get(f"/api/clients/{client_id}/storage").json()["hosted_by"] == "agency"  # connecting its own does not move anything
    assert _switch(client, client_id, "client").status_code == 200
    assert len(buckets[OWN["access_key_id"]].objects) == 1


def test_the_bucket_stays_while_clients_live_in_it(authenticated_client, buckets):
    client = authenticated_client
    _module(client, True)
    client_id = _acme(client)
    client.put("/api/agency/storage", json=AGENCY)
    assert _switch(client, client_id, "agency").status_code == 200
    assert client.get("/api/agency/storage").json()["clients_hosted"] == 1
    assert client.delete("/api/agency/storage").status_code == 409
    assert client.put("/api/agency/storage", json={**AGENCY, "bucket": "another-bucket"}).status_code == 409
    # The same bucket with refreshed credentials is fine.
    assert client.put("/api/agency/storage", json=AGENCY).status_code == 200


def test_switching_the_module_off_keeps_hosted_files_and_refuses_new_ones(authenticated_client, buckets):
    client = authenticated_client
    _module(client, True)
    inside, outside = _acme(client), client.post("/api/clients", json={"name": "Other", "is_active": True}).json()["id"]
    client.put("/api/agency/storage", json=AGENCY)
    client.put(f"/api/clients/{inside}/storage", json=OWN)
    assert _switch(client, inside, "agency").status_code == 200
    _module(client, False)
    assert _upload(client, inside, "Still", b"still works").status_code in (200, 201)
    assert _switch(client, outside, "agency").status_code == 403
    # Leaving is not an entry: it needs no module, only the client's own bucket.
    assert _switch(client, inside, "client").status_code == 200


def test_the_platform_moves_files_and_audits_it(authenticated_client, buckets):
    from test_platform_auth import _login, _platform_admin

    client = authenticated_client
    _module(client, True)
    client_id = _acme(client)
    client.put(f"/api/clients/{client_id}/storage", json=OWN)
    _upload(client, client_id)
    client.put("/api/agency/storage", json=AGENCY)
    platform = TestClient(client.app)
    _platform_admin()
    _login(platform)
    url = f"/api/platform/agencies/{_agency_id(client)}/clients/{client_id}/storage/switch"
    moved = platform.post(url, json={"target": "agency"})
    assert moved.status_code == 200, moved.text
    assert moved.json() == {"hosted_by": "agency", "copied": 1}
    assert platform.post(url, json={"target": "client"}).json()["hosted_by"] == "client"
    events = [e for e in platform.get("/api/platform/audit-events").json() if e["action"] == "client.files_moved"]
    assert sorted((e["details"]["from"], e["details"]["to"]) for e in events) == [("agency", "client"), ("client", "agency")]
    assert platform.post(url.replace(client_id, str(uuid.uuid4())), json={"target": "agency"}).status_code == 404
    with TestingSession() as db:
        assert db.scalar(select(ClientStorageConnection.hosted_by)) == "client"
