from __future__ import annotations

import os
from urllib.parse import urlparse

from minio import Minio


def get_minio_client() -> tuple[Minio, str]:
    endpoint = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    bucket = os.getenv("MINIO_BUCKET", "sentinel")

    parsed = urlparse(endpoint)
    hostport = parsed.netloc or parsed.path  # supports "http://x:9000" or "x:9000"
    secure = parsed.scheme == "https"

    client = Minio(
        hostport,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )
    return client, bucket