"""
Storage client for persisting extracted artifacts (images, etc.)

Tries MinIO first; falls back to local filesystem so the pipeline works
without Docker running.
"""
import os
from io import BytesIO


class StorageClient:

    def __init__(self):
        self._local_dir = os.getenv("LOCAL_STORAGE_DIR", "/tmp/insightforge_storage")
        self._minio = None
        self._bucket = os.getenv("MINIO_BUCKET", "insightforge")

        try:
            from minio import Minio
            client = Minio(
                os.getenv("MINIO_ENDPOINT", "localhost:9000"),
                access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
                secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
                secure=False,
            )
            if not client.bucket_exists(self._bucket):
                client.make_bucket(self._bucket)
            self._minio = client
        except Exception:
            os.makedirs(self._local_dir, exist_ok=True)

    @property
    def backend(self) -> str:
        return "minio" if self._minio else "local"

    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store bytes under key. Returns the URL/path to the stored object."""
        if self._minio:
            self._minio.put_object(
                self._bucket, key, BytesIO(data), len(data), content_type=content_type
            )
            endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
            return f"http://{endpoint}/{self._bucket}/{key}"
        else:
            # Local filesystem fallback
            safe_name = key.replace("/", "__")
            local_path = os.path.join(self._local_dir, safe_name)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(data)
            return f"file://{local_path}"
