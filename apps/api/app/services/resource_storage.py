"""Object storage in the client's own Cloudflare R2 bucket.

Every client that uses the resource library connects its own bucket (see
``services/storage_connection.py``); nothing is stored on the agency's
infrastructure. R2 speaks the S3 API, so this is plain boto3 pointed at
``https://<account id>.r2.cloudflarestorage.com``. The endpoint is built from
the account id and never typed by anyone, which keeps a customer from aiming
the server at an internal address.

boto3 is synchronous: async callers run these through ``asyncio.to_thread``.
"""

import re
import threading
import uuid

from fastapi import HTTPException

from ..models import Client, ClientStorageConnection
from ..security import decrypt_secret

# Hard ceilings a client's own limits can never exceed. WhatsApp itself caps an
# image at 5 MB and a video at 16 MB, and the bytes travel base64-encoded to
# Evolution, so the per-kind limits follow it.
MEDIA_LIMITS_MB = {"image": 5, "video": 16, "audio": 16, "file": 20}
CEILING_FILE_MB = 20
CEILING_QUOTA_MB = 10 * 1024

_ACCOUNT_ID = re.compile(r"^[0-9a-f]{32}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_MB = 1024 * 1024


class StorageError(Exception):
    """A bucket operation failed; the message is safe to show a person."""


def valid_account_id(value: str) -> bool:
    return bool(_ACCOUNT_ID.match(value or ""))


def valid_bucket(value: str) -> bool:
    return bool(_BUCKET.match(value or ""))


def endpoint_for(account_id: str) -> str:
    if not valid_account_id(account_id):
        raise StorageError("The Cloudflare account id must be 32 hexadecimal characters")
    return f"https://{account_id}.r2.cloudflarestorage.com"


def _build_client(account_id: str, access_key_id: str, secret: str, region: str):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_for(account_id),
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret,
        region_name=region or "auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
            s3={"addressing_style": "path"},
        ),
    )


class Storage:
    """One connected bucket."""

    def __init__(self, account_id: str, access_key_id: str, secret: str, bucket: str, region: str = "auto"):
        if not valid_bucket(bucket):
            raise StorageError("That is not a valid bucket name")
        self.bucket = bucket
        self._client = _build_client(account_id, access_key_id, secret, region)

    def _run(self, action: str, call):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return call()
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied", "403", "Unauthorized"}:
                raise StorageError("Cloudflare refused these credentials or the bucket permissions") from exc
            if code in {"NoSuchBucket", "404"}:
                raise StorageError("The bucket was not found in that Cloudflare account") from exc
            if code == "NoSuchKey":
                raise StorageError("The file is no longer in the bucket") from exc
            raise StorageError(f"The bucket answered with an error while trying to {action}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Could not reach Cloudflare R2 to {action}") from exc

    def put(self, key: str, data: bytes, mime: str) -> None:
        self._run(
            "save the file",
            lambda: self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=mime),
        )

    def get(self, key: str) -> bytes:
        return self._run(
            "read the file",
            lambda: self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read(),
        )

    def delete(self, key: str) -> None:
        self._run("delete the file", lambda: self._client.delete_object(Bucket=self.bucket, Key=key))

    def size(self, key: str) -> int:
        return int(self._run(
            "check the file", lambda: self._client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        ))

    def probe(self) -> None:
        """Write, read back and delete a throwaway object: proves the token can
        do everything the library needs."""
        key = f"_hunterai/probe-{uuid.uuid4().hex}"
        self.put(key, b"ok", "text/plain")
        try:
            if self.get(key) != b"ok":
                raise StorageError("The bucket returned different data than was written")
        finally:
            try:
                self.delete(key)
            except StorageError:
                pass


_cache: dict[tuple, Storage] = {}
_cache_lock = threading.Lock()


def storage_of(conn) -> Storage:
    """The bucket a connection record points at, built once and kept. Works for a
    client's own record and for the agency's: both carry the same columns."""
    if conn.status != "connected" or not conn.encrypted_access_key_id or not conn.encrypted_secret:
        raise HTTPException(status_code=409, detail="storage_not_connected")
    stamp = (conn.id, conn.updated_at, conn.bucket)
    with _cache_lock:
        cached = _cache.get(stamp)
        if cached:
            return cached
    try:
        store = Storage(
            conn.account_ref,
            decrypt_secret(conn.encrypted_access_key_id),
            decrypt_secret(conn.encrypted_secret),
            conn.bucket,
            conn.region,
        )
    except StorageError as exc:
        raise HTTPException(status_code=409, detail="storage_not_connected") from exc
    with _cache_lock:
        if len(_cache) > 256:
            _cache.clear()
        _cache[stamp] = store
    return store


def for_connection(conn: ClientStorageConnection) -> Storage:
    """The bucket of a connected client; a client that never connected has none.
    A client whose files the agency hosts reads and writes the agency's bucket."""
    if conn.hosted_by == "agency":
        from sqlalchemy import select
        from sqlalchemy.orm import object_session

        from ..models import AgencyStorageConnection

        session = object_session(conn)
        agency_conn = session.scalar(
            select(AgencyStorageConnection).where(AgencyStorageConnection.agency_id == conn.agency_id)
        ) if session is not None else None
        if agency_conn is None:
            raise HTTPException(status_code=409, detail="storage_not_connected")
        return storage_of(agency_conn)
    return storage_of(conn)


def for_client(client: Client) -> Storage:
    if not client.storage_connection:
        raise HTTPException(status_code=409, detail="storage_not_connected")
    return for_connection(client.storage_connection)


def max_bytes(conn: ClientStorageConnection, media_kind: str) -> int:
    """The largest file of this kind the client may upload: its own limit, never
    above the ceiling nor above what WhatsApp would take for that kind."""
    own = min(max(conn.max_file_mb, 1), CEILING_FILE_MB)
    return min(own, MEDIA_LIMITS_MB.get(media_kind, CEILING_FILE_MB)) * _MB


def quota_bytes(conn: ClientStorageConnection) -> int:
    return min(max(conn.quota_mb, 1), CEILING_QUOTA_MB) * _MB


def new_key(client: Client) -> str:
    """Object keys are opaque: nothing in them comes from the uploader."""
    return f"{client.agency_id}/{client.id}/{uuid.uuid4().hex}"
