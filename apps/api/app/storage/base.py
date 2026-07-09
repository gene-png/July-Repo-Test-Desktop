"""StorageBackend protocol + small data class."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


class StorageUnavailableError(RuntimeError):
    """Raised when the storage backend is unreachable or misconfigured.

    Distinct from FileNotFoundError (the object is simply missing): this
    signals a transient/infra problem - bad credentials, a connection
    timeout, an endpoint that won't answer. Routes map it to a 503 so the
    caller knows to retry, while a missing object maps to a 410.
    """


@dataclass(frozen=True)
class StoredObject:
    """Result of a successful put. Carries enough to populate the Artifact row."""

    key: str
    size_bytes: int
    sha256: str


def sha256_of(buf: bytes) -> str:
    return hashlib.sha256(buf).hexdigest()


class StorageBackend(Protocol):
    """Minimal surface for v1 artifact storage.

    The protocol stays narrow on purpose: artifacts are written once at
    upload time and read via signed URLs. Mutation isn't a v1 use case.
    """

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        """Write `data` to `key`. Returns size + sha256."""
        ...

    def get(self, key: str) -> bytes:
        """Return the raw bytes at `key`. Raises FileNotFoundError if missing."""
        ...

    def exists(self, key: str) -> bool: ...

    def signed_url(self, key: str, *, ttl_seconds: int = 600) -> str:
        """Return a short-lived read URL for the object at `key`."""
        ...
