"""File storage adapter, chosen by STORAGE_BACKEND (local | s3).

This and auth/oidc.py are the only modules allowed to contain cloud-specific code. The S3 backend
works with AWS S3, GCS (S3 interop), MinIO and any S3-compatible bucket.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.config import get_settings


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> str:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def list(self, prefix: str) -> list[str]:
        base = self._p(prefix)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())


class S3Storage:
    def __init__(self):
        import boto3  # only imported when the S3 backend is chosen

        s = get_settings()
        self.bucket = s.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=s.S3_ENDPOINT_URL or None,
            aws_access_key_id=s.S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=s.S3_SECRET_ACCESS_KEY or None,
            region_name=s.S3_REGION or None,
        )

    def put(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def list(self, prefix: str) -> list[str]:
        keys, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self.client.list_objects_v2(**kw)
            keys += [o["Key"] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return sorted(keys)
            token = r["NextContinuationToken"]


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        s = get_settings()
        if s.STORAGE_BACKEND == "s3":
            _storage = S3Storage()
        else:
            root = s.STORAGE_LOCAL_PATH
            try:
                os.makedirs(root, exist_ok=True)
            except PermissionError:
                root = os.path.abspath("./data")
                os.makedirs(root, exist_ok=True)
            _storage = LocalStorage(root)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
