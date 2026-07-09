"""S3 / S3-compatible storage backend (MinIO in dev; AWS S3 + KMS in prod).

boto3 is a heavy import; resolve it lazily so test runs that never touch
S3 don't pay the import cost.
"""

from __future__ import annotations

from app.config import Settings
from app.storage.base import StorageBackend, StorageUnavailableError, StoredObject, sha256_of

# Bounded client budget so a wedged endpoint fails fast instead of hanging the
# request thread (C-7). Kept as module constants rather than Settings fields to
# avoid growing the config surface.
_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 30
_MAX_ATTEMPTS = 2

# boto error codes that mean "the object isn't there" (vs. an infra failure).
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket", "404"}


class S3Storage(StorageBackend):
    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = settings.s3_bucket
        self._kms_key_id = settings.s3_kms_key_id
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(
                signature_version="s3v4",
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_READ_TIMEOUT_SECONDS,
                retries={"max_attempts": _MAX_ATTEMPTS},
            ),
        )

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        kwargs: dict = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        # KMS encryption is mandatory in production. The dev-stub key id
        # short-circuits the SSE arg so MinIO doesn't reject the request.
        if self._kms_key_id and self._kms_key_id != "dev-stub-key":
            kwargs["ServerSideEncryption"] = "aws:kms"
            kwargs["SSEKMSKeyId"] = self._kms_key_id
        self._client.put_object(**kwargs)
        return StoredObject(key=key, size_bytes=len(data), sha256=sha256_of(data))

    def get(self, key: str) -> bytes:
        from botocore.exceptions import (
            ClientError,
            EndpointConnectionError,
            NoCredentialsError,
        )

        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in _NOT_FOUND_CODES or status == 404:
                raise FileNotFoundError(key) from exc
            # Auth / throttling / server errors are infra problems, not a
            # missing object: surface as a retryable outage.
            raise StorageUnavailableError(
                f"S3 get_object failed for {key!r} ({code or status})"
            ) from exc
        except (EndpointConnectionError, NoCredentialsError) as exc:
            raise StorageUnavailableError(f"S3 backend is unreachable for {key!r}: {exc}") from exc
        return resp["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 - boto raises a family of errors here
            return False

    def signed_url(self, key: str, *, ttl_seconds: int = 600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )
