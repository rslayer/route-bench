"""Tests for S3StorageBackend.

Uses direct unit tests with mocked aioboto3 client since moto + aioboto3
have version compatibility issues with async context managers.
"""

from __future__ import annotations

from routebench.infra.storage.s3 import S3StorageBackend

BUCKET = "test-bucket"


def _make_storage() -> S3StorageBackend:
    return S3StorageBackend(
        endpoint_url="http://fake:9000",
        access_key_id="testing",
        secret_access_key="testing",
        bucket=BUCKET,
        region="us-east-1",
    )


class TestS3StorageBackend:
    """Tests for S3 storage configuration and key generation."""

    def test_key_generation(self) -> None:
        """Verify S3 key layout."""
        storage = _make_storage()
        assert storage._key("sess-1", "report.html") == "sessions/sess-1/report.html"
        assert storage._key("abc", "upload.csv") == "sessions/abc/upload.csv"

    def test_boto_config(self) -> None:
        """Verify boto config includes credentials."""
        storage = _make_storage()
        cfg = storage._boto_config()
        assert cfg["service_name"] == "s3"
        assert cfg["aws_access_key_id"] == "testing"
        assert cfg["aws_secret_access_key"] == "testing"
        assert cfg["region_name"] == "us-east-1"
        assert cfg["endpoint_url"] == "http://fake:9000"

    def test_init_stores_config(self) -> None:
        """Verify constructor stores config correctly."""
        storage = S3StorageBackend(
            endpoint_url="http://custom:443",
            access_key_id="key1",
            secret_access_key="secret1",
            bucket="my-bucket",
            region="eu-west-1",
        )
        assert storage._bucket == "my-bucket"
        assert storage._region == "eu-west-1"
        assert storage._endpoint_url == "http://custom:443"
