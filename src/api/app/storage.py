from __future__ import annotations

import os
from urllib.parse import urlparse

from minio import Minio

MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "sentinel")


def get_minio_client() -> Minio:
    endpoint = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")

    parsed = urlparse(endpoint)
    hostport = parsed.netloc or parsed.path  # supports "http://x:9000" or "x:9000"
    secure = parsed.scheme == "https"

    return Minio(
        hostport,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def ensure_bucket(client: Minio) -> None:
    if not client.bucket_exists(MINIO_BUCKET):
        client.make_bucket(MINIO_BUCKET)
