#!/usr/bin/env python3
"""Mirror the SHIELD artifacts object store to/from a local directory.

Dependency-light backup helper for H-3. Uses boto3 against the ``S3_*`` env
vars (which point at MinIO in dev and real S3 + KMS in prod), so no ``mc`` /
``aws`` CLI is required.

Modes:
  (default)     mirror the bucket DOWN into ``--dir`` (backup).
  ``--reverse`` push objects UP from ``--dir`` into the bucket (restore).

Env (see ``.env.example`` / ``docker-compose.yml``):
  S3_ENDPOINT_URL   e.g. http://localhost:9000 (omit/empty for real AWS)
  S3_BUCKET         e.g. shield-artifacts
  S3_ACCESS_KEY / S3_SECRET_KEY
  S3_REGION         optional; defaults to us-east-1 (MinIO ignores it)

Exit code is non-zero on any transfer error so callers (backup.sh /
restore.sh) fail loudly.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _client():
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("S3_ENDPOINT_URL") or None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )


def _bucket() -> str:
    bucket = os.environ.get("S3_BUCKET")
    if not bucket:
        print("object_sync: S3_BUCKET is not set", file=sys.stderr)
        raise SystemExit(2)
    return bucket


def mirror_down(dest: Path) -> int:
    """Download every object in the bucket into ``dest`` (backup)."""
    client = _client()
    bucket = _bucket()
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            target = dest / key
            target.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(target))
            count += 1
    print(f"object_sync: mirrored {count} object(s) from s3://{bucket} -> {dest}")
    return count


def mirror_up(src: Path) -> int:
    """Upload every file under ``src`` into the bucket (restore)."""
    client = _client()
    bucket = _bucket()
    if not src.is_dir():
        print(f"object_sync: source dir {src} does not exist", file=sys.stderr)
        raise SystemExit(2)
    count = 0
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(src).as_posix()
        client.upload_file(str(path), bucket, key)
        count += 1
    print(f"object_sync: mirrored {count} object(s) from {src} -> s3://{bucket}")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="local mirror directory")
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="push local objects back into the bucket (restore)",
    )
    args = parser.parse_args(argv)
    directory = Path(args.dir).expanduser().resolve()
    if args.reverse:
        mirror_up(directory)
    else:
        mirror_down(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
