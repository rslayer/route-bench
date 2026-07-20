"""S3-compatible StorageBackend (Cloudflare R2, AWS S3, MinIO)."""

from __future__ import annotations

from typing import Any

import aioboto3
import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class S3StorageBackend:
    """Stores session artifacts in an S3-compatible bucket.

    Layout: sessions/{session_id}/{filename}
    Reuses a single aioboto3 Session to avoid per-call connection churn.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        region: str = "auto",
    ) -> None:
        self._endpoint_url = endpoint_url
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._region = region
        self._session = aioboto3.Session()

    def _boto_config(self) -> dict[str, Any]:
        return {
            "service_name": "s3",
            "endpoint_url": self._endpoint_url,
            "aws_access_key_id": self._access_key_id,
            "aws_secret_access_key": self._secret_access_key,
            "region_name": self._region,
        }

    def _key(self, session_id: str, filename: str) -> str:
        return f"sessions/{session_id}/{filename}"

    async def write(self, session_id: str, filename: str, data: bytes) -> None:
        async with self._session.client(**self._boto_config()) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=self._key(session_id, filename),
                Body=data,
            )

    async def read(self, session_id: str, filename: str) -> bytes:
        async with self._session.client(**self._boto_config()) as s3:
            try:
                resp = await s3.get_object(
                    Bucket=self._bucket,
                    Key=self._key(session_id, filename),
                )
                body = await resp["Body"].read()
                return bytes(body)
            except Exception as exc:
                if "NoSuchKey" in str(exc) or "404" in str(exc):
                    msg = f"File not found: {session_id}/{filename}"
                    raise FileNotFoundError(msg) from exc
                raise

    async def exists(self, session_id: str, filename: str) -> bool:
        async with self._session.client(**self._boto_config()) as s3:
            try:
                await s3.head_object(
                    Bucket=self._bucket,
                    Key=self._key(session_id, filename),
                )
                return True
            except Exception:
                return False

    async def presigned_url(self, session_id: str, filename: str, ttl_seconds: int = 900) -> str:
        async with self._session.client(**self._boto_config()) as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": self._key(session_id, filename),
                },
                ExpiresIn=ttl_seconds,
            )
            return url

    async def delete_file(self, session_id: str, filename: str) -> bool:
        key = self._key(session_id, filename)
        async with self._session.client(**self._boto_config()) as s3:
            # delete_object succeeds on a missing key, so existence has to be
            # checked separately for the return value to mean anything.
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
            except Exception:
                return False
            await s3.delete_object(Bucket=self._bucket, Key=key)
            return True

    async def delete_session(self, session_id: str) -> None:
        prefix = f"sessions/{session_id}/"
        async with self._session.client(**self._boto_config()) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                contents = page.get("Contents", [])
                if contents:
                    await s3.delete_objects(
                        Bucket=self._bucket,
                        Delete={
                            "Objects": [{"Key": obj["Key"]} for obj in contents],
                        },
                    )
        logger.info("session_deleted_s3", session_id=session_id)

    async def list_sessions(self) -> list[str]:
        sessions: set[str] = set()
        async with self._session.client(**self._boto_config()) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self._bucket, Prefix="sessions/", Delimiter="/"
            ):
                for cp in page.get("CommonPrefixes", []):
                    prefix = cp["Prefix"]
                    # sessions/abc123/ -> abc123
                    parts = prefix.rstrip("/").split("/")
                    if len(parts) >= 2:
                        sessions.add(parts[1])
        return sorted(sessions)

    async def is_writable(self) -> bool:
        try:
            async with self._session.client(**self._boto_config()) as s3:
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=".probe",
                    Body=b"ok",
                )
                await s3.delete_object(Bucket=self._bucket, Key=".probe")
            return True
        except Exception:
            logger.exception("s3_write_check_failed")
            return False

    async def read_object(self, key: str) -> bytes:
        """Read a non-session object. Keys live at the bucket root, beside sessions/."""
        async with self._session.client(**self._boto_config()) as s3:
            try:
                resp = await s3.get_object(Bucket=self._bucket, Key=key)
                body = await resp["Body"].read()
                return bytes(body)
            except Exception as exc:
                if "NoSuchKey" in str(exc) or "404" in str(exc):
                    msg = f"Object not found: {key}"
                    raise FileNotFoundError(msg) from exc
                raise

    async def append_object(self, key: str, data: bytes) -> None:
        """Append via read-modify-write — S3 has no native append.

        Safe only because callers serialize appends to a key and a deployment
        runs one worker per bucket path. Concurrent writers would lose updates;
        multi-machine coordination is explicitly out of scope.
        """
        try:
            existing = await self.read_object(key)
        except FileNotFoundError:
            existing = b""

        async with self._session.client(**self._boto_config()) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=existing + data)
