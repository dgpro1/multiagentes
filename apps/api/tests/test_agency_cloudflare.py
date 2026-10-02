"""Connecting the agency's bucket from one Cloudflare API token.

Cloudflare is replaced by an in-memory transport, so nothing leaves the machine.
"""

import hashlib
import json

import httpx
import pytest

from app.services import agency_backend, cloudflare_mgmt, resource_storage
from test_agency_storage import ACCOUNT, _module, buckets  # noqa: F401 - buckets is a fixture

TOKEN = "cf-token-that-is-long-enough-123456"


def _cloudflare(monkeypatch, *, groups=None, status=200, bucket_exists=False, accounts=None):
    seen: list[tuple[str, str, dict]] = []
    groups = [{"id": "grp-1", "name": cloudflare_mgmt.PERMISSION}] if groups is None else groups
    accounts = [{"id": ACCOUNT}] if accounts is None else accounts

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, request.url.path, body))
        if status != 200:
            return httpx.Response(status, json={"success": False, "errors": [{"code": 9109}]})
        path = request.url.path
        if path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "result": accounts})
        if path.endswith("/permission_groups"):
            return httpx.Response(200, json={"success": True, "result": groups})
        if path.endswith("/r2/buckets"):
            if bucket_exists:
                return httpx.Response(409, json={"success": False, "errors": [{"code": 10004}]})
            return httpx.Response(200, json={"success": True, "result": {"name": body["name"]}})
        if path.endswith("/tokens"):
            return httpx.Response(200, json={"success": True, "result": {"id": "key-id-1234567890", "value": "token-value-abc"}})
        return httpx.Response(404, json={"success": False, "errors": []})

    real = httpx.Client
    monkeypatch.setattr(cloudflare_mgmt.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


def test_the_token_creates_the_bucket_and_a_key_that_can_only_use_it(monkeypatch):
    seen = _cloudflare(monkeypatch)
    account, key_id, secret = cloudflare_mgmt.provision(TOKEN, "agency-files")
    assert (account, key_id) == (ACCOUNT, "key-id-1234567890")
    assert secret == hashlib.sha256(b"token-value-abc").hexdigest()
    created = next(body for method, path, body in seen if path.endswith("/tokens") and method == "POST")
    policy = created["policies"][0]
    assert policy["resources"] == {f"com.cloudflare.edge.r2.bucket.{ACCOUNT}_default_agency-files": "*"}
    assert policy["permission_groups"] == [{"id": "grp-1"}]


def test_a_bucket_that_already_exists_is_reused(monkeypatch):
    _cloudflare(monkeypatch, bucket_exists=True)
    assert cloudflare_mgmt.provision(TOKEN, "agency-files", ACCOUNT)[0] == ACCOUNT


@pytest.mark.parametrize("kwargs, message", [
    ({"status": 403}, "Cloudflare did not accept that token or it lacks permissions"),
    ({"groups": []}, "Cloudflare did not accept that token or it lacks permissions"),
    ({"accounts": [{"id": ACCOUNT}, {"id": "f" * 32}]}, "That token sees several Cloudflare accounts: enter the account id"),
])
def test_a_token_that_cannot_do_it_says_why(monkeypatch, kwargs, message):
    _cloudflare(monkeypatch, **kwargs)
    with pytest.raises(resource_storage.StorageError, match=message):
        cloudflare_mgmt.provision(TOKEN, "agency-files")


def test_the_agency_connects_with_one_token_and_keeps_none_of_it(authenticated_client, buckets, monkeypatch):  # noqa: F811
    client = authenticated_client
    monkeypatch.setattr(agency_backend, "PROPAGATION_WAIT", 0)
    monkeypatch.setattr(cloudflare_mgmt, "provision", lambda token, bucket, account_id="": (ACCOUNT, "CFKEY-1234567890", "cf-secret-never-shown"))
    assert client.put("/api/agency/storage/cloudflare", json={"token": TOKEN}).status_code == 403  # the module ships off
    _module(client, True)
    response = client.put("/api/agency/storage/cloudflare", json={"token": TOKEN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["account_id"]) == ("connected", ACCOUNT)
    assert body["bucket"].startswith("hunterai-")
    assert TOKEN not in response.text and "cf-secret-never-shown" not in response.text and "CFKEY-1234567890" not in response.text


def test_a_failure_at_cloudflare_connects_nothing(authenticated_client, buckets, monkeypatch):  # noqa: F811
    client = authenticated_client
    _module(client, True)

    def refuse(token, bucket, account_id=""):
        raise resource_storage.StorageError("Cloudflare did not accept that token or it lacks permissions")

    monkeypatch.setattr(cloudflare_mgmt, "provision", refuse)
    response = client.put("/api/agency/storage/cloudflare", json={"token": TOKEN})
    assert response.status_code == 422
    assert client.get("/api/agency/storage").json()["status"] == "none"
    assert client.put("/api/agency/storage/cloudflare", json={"token": "short"}).status_code == 422
