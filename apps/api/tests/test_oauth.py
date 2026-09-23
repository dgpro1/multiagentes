"""OAuth 2.0 authorization-code flow on the API credential tables."""

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi.testclient import TestClient

from conftest import TestingSession


def _integration(client: TestClient, scopes: list[str] | None = None) -> dict:
    payload = {"name": "n8n", "preset": "read_only"}
    if scopes is not None:
        payload = {"name": "n8n", "scopes": scopes}
    created = client.post("/api/integrations", json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def _oauth_client(client: TestClient, integration_id: str, uris: list[str] | None = None) -> dict:
    response = client.post(
        f"/api/integrations/{integration_id}/oauth-client",
        json={"redirect_uris": uris if uris is not None else ["https://app.example/cb"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _setup(client: TestClient) -> tuple[dict, dict]:
    integration = _integration(client, scopes=["clients.read", "inbox.read"])
    oauth = _oauth_client(client, integration["id"])
    return integration, oauth


def _approve(client: TestClient, oauth: dict, scope: str = "", state: str = "xyz") -> str:
    response = client.post("/api/oauth/authorize", json={
        "client_id": oauth["oauth_client_id"],
        "redirect_uri": "https://app.example/cb",
        "scope": scope,
        "state": state,
        "approved": True,
    })
    assert response.status_code == 200, response.text
    return response.json()["redirect_to"]


def _exchange(client: TestClient, oauth: dict, code: str, secret: str | None = None) -> TestClient:
    response = client.post("/api/oauth/token", json={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://app.example/cb",
        "client_id": oauth["oauth_client_id"],
        "client_secret": secret if secret is not None else oauth["client_secret"],
    })
    return response


def test_oauth_client_setup_and_rotation(authenticated_client: TestClient):
    client = authenticated_client
    integration = _integration(client)
    first = _oauth_client(client, integration["id"])
    assert first["oauth_client_id"].startswith("olic_")
    assert first["client_secret"].startswith("ol_cs_")
    assert first["redirect_uris"] == ["https://app.example/cb"]
    # Rotating replaces the secret and the list.
    second = _oauth_client(client, integration["id"], ["https://app.example/other"])
    assert second["oauth_client_id"] == first["oauth_client_id"]
    assert second["client_secret"] != first["client_secret"]
    assert second["redirect_uris"] == ["https://app.example/other"]
    # http is only for loopback development.
    assert client.post(f"/api/integrations/{integration['id']}/oauth-client",
                       json={"redirect_uris": ["http://evil.test/cb"]}).status_code == 400
    assert client.post(f"/api/integrations/{integration['id']}/oauth-client",
                       json={"redirect_uris": [f"https://x.test/{n}" for n in range(11)]}).status_code == 400
    assert client.post(f"/api/integrations/{integration['id']}/oauth-client",
                       json={"redirect_uris": ["http://localhost:3000/cb"]}).status_code == 201


def test_authorize_validates_before_redirecting(authenticated_client: TestClient):
    client = authenticated_client
    _, oauth = _setup(client)
    base = {"client_id": oauth["oauth_client_id"], "redirect_uri": "https://app.example/cb", "state": "s"}
    # Unknown client or unregistered redirect: no redirect to trust.
    assert client.post("/api/oauth/authorize", json={**base, "client_id": "olic_nope", "approved": True}).status_code == 400
    assert client.post("/api/oauth/authorize", json={**base, "redirect_uri": "https://evil.test/cb", "approved": True}).status_code == 400
    # Unknown scopes are refused without redirecting anywhere blind.
    denied = client.post("/api/oauth/authorize", json={**base, "scope": "nope.read", "approved": True})
    assert denied.status_code == 200
    assert "error=invalid_scope" in denied.json()["redirect_to"]
    # Denial lands on the registered redirect with access_denied.
    refused = client.post("/api/oauth/authorize", json={**base, "approved": False})
    assert refused.status_code == 200
    assert refused.json()["redirect_to"].startswith("https://app.example/cb?error=access_denied&state=s")


def test_full_code_flow_and_single_use(authenticated_client: TestClient):
    client = authenticated_client
    _, oauth = _setup(client)
    redirect_to = _approve(client, oauth)
    assert redirect_to.startswith("https://app.example/cb?code=ol_") and "state=xyz" in redirect_to
    code = redirect_to.split("code=")[1].split("&")[0]

    tokens = _exchange(client, oauth, code)
    assert tokens.status_code == 200, tokens.text
    body = tokens.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600
    assert set(body["scope"].split()) == {"clients.read", "inbox.read"}

    # The code is single-use and bound to its redirect.
    assert _exchange(client, oauth, code).status_code == 400
    other = _approve(client, oauth, state="s2")
    other_code = other.split("code=")[1].split("&")[0]
    retry = client.post("/api/oauth/token", json={
        "grant_type": "authorization_code", "code": other_code, "redirect_uri": "https://evil.test/cb",
        "client_id": oauth["oauth_client_id"], "client_secret": oauth["client_secret"],
    })
    assert retry.status_code == 400

    # The access token opens scoped routes, and codes never work as bearers.
    assert client.get("/api/clients", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200
    assert client.get("/api/clients", headers={"Authorization": f"Bearer {other_code}"}).status_code == 401
    # Wrong secret is a 401, not a hint.
    assert _exchange(client, oauth, other_code, secret="ol_cs_wrong").status_code == 401


def test_granted_subset_limits_the_tokens(authenticated_client: TestClient):
    client = authenticated_client
    _, oauth = _setup(client)
    redirect_to = _approve(client, oauth, scope="clients.read")
    code = redirect_to.split("code=")[1].split("&")[0]
    body = _exchange(client, oauth, code).json()
    assert body["scope"] == "clients.read"
    assert client.get("/api/clients", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200
    assert client.get("/api/conversations/inbox", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 403


def test_refresh_rotates_and_revoke_kills_the_grant(authenticated_client: TestClient):
    client = authenticated_client
    _, oauth = _setup(client)
    code = _approve(client, oauth).split("code=")[1].split("&")[0]
    first = _exchange(client, oauth, code).json()

    def refresh(token: str):
        return client.post("/api/oauth/token", json={
            "grant_type": "refresh_token", "refresh_token": token,
            "client_id": oauth["oauth_client_id"], "client_secret": oauth["client_secret"],
        })

    second = refresh(first["refresh_token"])
    assert second.status_code == 200, second.text
    # Rotation retires the old pair.
    assert refresh(first["refresh_token"]).status_code == 400
    assert client.get("/api/clients", headers={"Authorization": f"Bearer {first['access_token']}"}).status_code == 401
    rotated = second.json()
    assert rotated["refresh_token"] != first["refresh_token"]
    assert client.get("/api/clients", headers={"Authorization": f"Bearer {rotated['access_token']}"}).status_code == 200

    # Revoking the refresh token kills the rotated access token too.
    revoked = client.post("/api/oauth/revoke", json={
        "token": rotated["refresh_token"], "client_id": oauth["oauth_client_id"],
        "client_secret": oauth["client_secret"],
    })
    assert revoked.status_code == 200
    assert client.get("/api/clients", headers={"Authorization": f"Bearer {rotated['access_token']}"}).status_code == 401
    assert refresh(rotated["refresh_token"]).status_code == 400
    # Unknown tokens still answer 200.
    assert client.post("/api/oauth/revoke", json={
        "token": "ol_nope", "client_id": oauth["oauth_client_id"],
        "client_secret": oauth["client_secret"],
    }).status_code == 200


def test_expired_and_revoked_grants_fail(authenticated_client: TestClient):
    from app.models import ApiToken

    client = authenticated_client
    _, oauth = _setup(client)
    code = _approve(client, oauth).split("code=")[1].split("&")[0]
    with TestingSession() as db:
        row = db.execute(sa.select(ApiToken).where(ApiToken.kind == "auth_code")).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert _exchange(client, oauth, code).status_code == 400

    code = _approve(client, oauth, state="again").split("code=")[1].split("&")[0]
    with TestingSession() as db:
        db.execute(
            sa.update(ApiToken).where(ApiToken.kind == "auth_code").values(revoked_at=datetime.now(timezone.utc))
        )
        db.commit()
    assert _exchange(client, oauth, code).status_code == 400


def test_consent_client_info_is_agency_scoped(authenticated_client: TestClient):
    from conftest import login_legacy_owner

    client = authenticated_client
    _, oauth = _setup(client)
    info = client.get("/api/oauth/client", params={"client_id": oauth["oauth_client_id"]})
    assert info.status_code == 200, info.text
    assert info.json()["name"] == "n8n" and "clients.read" in info.json()["scopes"]
    assert info.json()["scope_descriptions"]["clients.read"]
    login_legacy_owner(client)
    assert client.get("/api/oauth/client", params={"client_id": oauth["oauth_client_id"]}).status_code == 404
