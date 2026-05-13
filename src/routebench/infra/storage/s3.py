"""S3-compatible StorageBackend (Cloudflare R2, AWS S3, MinIO)."""

from __future__ import annotations

from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class S3StorageBackend:
    """Stores session artifacts in an S3-compatible bucket.

    Layout: sessions/{session_id}/{filename}
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
        import aioboto3

        session = aioboto3.Session()
        async with session.client(**self._boto_config()) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=self._key(session_id, filename),
                Body=data,
            )

    async def read(self, session_id: str, filename: str) -> bytes:
        import aioboto3

        session = aioboto3.Session()
        async with session.client(**self._boto_config()) as s3:
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
        import aioboto3

        session = aioboto3.Session()
        async with session.client(**self._boto_config()) as s3:
            try:
                await s3.head_object(
                    Bucket=self._bucket,
                    Key=self._key(session_id, filename),
                )
                return True
            except Exception:
                return False

    async def presigned_url(self, session_id: str, filename: str, ttl_seconds: int = 900) -> str:
        import aioboto3

        session = aioboto3.Session()
        async with session.client(**self._boto_config()) as s3:
            url: str = await s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": self._key(session_id, filename),
                },
                ExpiresIn=ttl_seconds,
            )
            return url

    async def delete_session(self, session_id: str) -> None:
        import aioboto3

        session = aioboto3.Session()
        prefix = f"sessions/{session_id}/"
        async with session.client(**self._boto_config()) as s3:
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
        import aioboto3

        session = aioboto3.Session()
        sessions: set[str] = set()
        async with session.client(**self._boto_config()) as s3:
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
        import aioboto3

        session = aioboto3.Session()
        try:
            async with session.client(**self._boto_config()) as s3:
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
