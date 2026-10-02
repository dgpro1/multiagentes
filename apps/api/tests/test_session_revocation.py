"""Signing out ends the session on the server, so a copied cookie stops working."""

import jwt

from app.config import get_settings


def test_a_copied_session_stops_working_after_sign_out(authenticated_client):
    client = authenticated_client
    copied = client.cookies.get("access_token")
    assert copied
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set("access_token", copied)
    assert client.get("/api/auth/me").status_code == 401


def test_a_session_from_before_versions_existed_still_works(authenticated_client):
    client = authenticated_client
    token = client.cookies.get("access_token")
    payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    payload.pop("ver")
    client.cookies.set("access_token", jwt.encode(payload, get_settings().secret_key, algorithm="HS256"))
    assert client.get("/api/auth/me").status_code == 200


def test_signing_out_twice_or_without_a_session_is_harmless(authenticated_client):
    client = authenticated_client
    assert client.post("/api/auth/logout").status_code == 204
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.clear()
    assert client.post("/api/auth/logout").status_code == 204
