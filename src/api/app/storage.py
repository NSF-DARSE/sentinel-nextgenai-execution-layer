from __future__ import annotations

from __future__ import annotations

import io
import os
from typing import Protocol, Any, Iterable
from urllib.parse import urlparse

from minio import Minio
from minio.deleteobjects import DeleteObject

try:
    from google.cloud import storage as gcs
except ImportError:
    gcs = None

BUCKET_NAME: str = os.getenv("STORAGE_BUCKET", os.getenv("MINIO_BUCKET", "sentinel"))


class StorageClient(Protocol):
    def get_object(self, bucket_name: str, object_name: str) -> io.BytesIO:
        ...

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: io.BytesIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> None:
        ...

    def list_objects(self, bucket_name: str, prefix: str) -> Iterable[Any]:
        ...

    def remove_objects(self, bucket_name: str, object_names: Iterable[str]) -> None:
        ...

    def bucket_exists(self, bucket_name: str) -> bool:
        ...

    def make_bucket(self, bucket_name: str) -> None:
        ...


class MinioStorageClient:
    def __init__(self):
        endpoint = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
        access_key = os.getenv("MINIO_ACCESS_KEY", "sentinel")
        secret_key = os.getenv("MINIO_SECRET_KEY", "sentinel123")

        parsed = urlparse(endpoint)
        hostport = parsed.netloc or parsed.path
        secure = parsed.scheme == "https"

        self.client = Minio(
            hostport,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def get_object(self, bucket_name: str, object_name: str) -> io.BytesIO:
        response = self.client.get_object(bucket_name, object_name)
        try:
            return io.BytesIO(response.read())
        finally:
            response.close()
            response.release_conn()

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: io.BytesIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.client.put_object(bucket_name, object_name, data, length, content_type)

    def list_objects(self, bucket_name: str, prefix: str) -> Iterable[Any]:
        return self.client.list_objects(bucket_name, prefix=prefix)

    def remove_objects(self, bucket_name: str, object_names: Iterable[str]) -> None:
        delete_objs = [DeleteObject(name) for name in object_names]
        errors = self.client.remove_objects(bucket_name, delete_objs)
        for err in errors:
            raise RuntimeError(f"Failed to delete {err.object_name}: {err.message}")

    def bucket_exists(self, bucket_name: str) -> bool:
        return self.client.bucket_exists(bucket_name)

    def make_bucket(self, bucket_name: str) -> None:
        self.client.make_bucket(bucket_name)


class GCSStorageClient:
    def __init__(self):
        if gcs is None:
            raise ImportError("google-cloud-storage not installed")
        self.client = gcs.Client()

    def get_object(self, bucket_name: str, object_name: str) -> io.BytesIO:
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        return io.BytesIO(blob.download_as_bytes())

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: io.BytesIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> None:
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        blob.upload_from_file(data, content_type=content_type)

    def list_objects(self, bucket_name: str, prefix: str) -> Iterable[Any]:
        # Return object-like items for compatibility with worker.py obj.object_name
        class GCSObject:
            def __init__(self, name):
                self.object_name = name

        blobs = self.client.list_blobs(bucket_name, prefix=prefix)
        return [GCSObject(blob.name) for blob in blobs]

    def remove_objects(self, bucket_name: str, object_names: Iterable[str]) -> None:
        bucket = self.client.bucket(bucket_name)
        # GCS delete_blobs handles a list of names
        bucket.delete_blobs(list(object_names))

    def bucket_exists(self, bucket_name: str) -> bool:
        return self.client.bucket(bucket_name).exists()

    def make_bucket(self, bucket_name: str) -> None:
        self.client.create_bucket(bucket_name)


def get_storage_client() -> StorageClient:
    if os.getenv("USE_GCS", "false").lower() == "true":
        return GCSStorageClient()
    return MinioStorageClient()


def ensure_bucket(client: StorageClient) -> None:
    if not client.bucket_exists(BUCKET_NAME):
        client.make_bucket(BUCKET_NAME)
