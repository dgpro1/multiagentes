"""Create an agency's R2 bucket and its access keys from one Cloudflare API token.

Typing an account id, a bucket and an S3 key pair is six fields; this turns it into
one pasted token. The token needs two account permissions (Workers R2 Storage: Edit
and Account API Tokens: Edit). With it HunterAI creates the bucket and a second
token that can read and write that bucket only. The wide token is used once and
never kept; only the narrow key pair is stored, exactly as if the agency had pasted it.

R2's S3 credentials are derived from an API token: the access key id is the
token's id and the secret access key is the SHA-256 of its value.
"""

import hashlib
import secrets

import httpx

from .resource_storage import StorageError

API = "https://api.cloudflare.com/client/v4"
PERMISSION = "Workers R2 Storage Bucket Item Write"
TIMEOUT = 20.0


def _call(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    try:
        response = client.request(method, f"{API}{path}", **kwargs)
    except httpx.HTTPError as exc:
        raise StorageError("Cloudflare did not answer") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code in (401, 403):
        raise StorageError("Cloudflare did not accept that token or it lacks permissions")
    if not body.get("success"):
        codes = [error.get("code") for error in body.get("errors") or []]
        raise StorageError("Cloudflare could not complete that request") if codes != [10004] else StorageError("bucket exists")
    return body


def _account_id(client: httpx.Client) -> str:
    accounts = _call(client, "GET", "/accounts").get("result") or []
    if len(accounts) != 1:
        raise StorageError("That token sees several Cloudflare accounts: enter the account id")
    return accounts[0]["id"]


def provision(token: str, bucket: str, account_id: str = "") -> tuple[str, str, str]:
    """Return (account id, access key id, secret access key) for a bucket this token created."""
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT) as client:
        account = account_id or _account_id(client)
        groups = _call(client, "GET", f"/accounts/{account}/tokens/permission_groups").get("result") or []
        group = next((g["id"] for g in groups if g.get("name") == PERMISSION), None)
        if not group:
            raise StorageError("Cloudflare did not accept that token or it lacks permissions")
        try:
            _call(client, "POST", f"/accounts/{account}/r2/buckets", json={"name": bucket})
        except StorageError as exc:
            if str(exc) != "bucket exists":
                raise
        created = _call(client, "POST", f"/accounts/{account}/tokens", json={
            "name": f"hunterai-{bucket}-{secrets.token_hex(3)}",
            "policies": [{
                "effect": "allow",
                "resources": {f"com.cloudflare.edge.r2.bucket.{account}_default_{bucket}": "*"},
                "permission_groups": [{"id": group}],
            }],
        }).get("result") or {}
    if not created.get("id") or not created.get("value"):
        raise StorageError("Cloudflare could not complete that request")
    return account, created["id"], hashlib.sha256(created["value"].encode()).hexdigest()
