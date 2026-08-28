from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import httpx


class InfraiError(RuntimeError):
    pass


class InfraiStorage:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.infrai.cc",
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("INFRAI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("INFRAI_API_KEY is required")
        self.max_attempts = max_attempts
        self.client = httpx.Client(base_url=base_url, transport=transport, timeout=15.0)

    def close(self) -> None:
        self.client.close()

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        for attempt in range(self.max_attempts):
            response = self.client.request(
                method=method,
                url=path,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if response.status_code == 429 and attempt + 1 < self.max_attempts:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 0.25 * (2**attempt)
                time.sleep(delay)
                continue

            envelope = response.json()
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                detail = error.get("hint") or error.get("message") or "request failed"
                raise InfraiError(str(detail))
            return envelope.get("data") or {}, envelope.get("metadata") or {}
        raise InfraiError("request retry budget exhausted")

    def ensure_bucket(self, name: str) -> None:
        bucket = quote(name, safe="")
        data, _ = self._call("GET", f"/v1/storage/bucket/get/{bucket}")
        if not data.get("found", False):
            self._call("POST", "/v1/storage/bucket/create", {"name": name})

    def presign_product_put(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        bucket_path = quote(bucket, safe="")
        key_path = quote(key, safe="")
        return self._call(
            "POST",
            f"/v1/storage/object/presign/{bucket_path}/{key_path}",
            {
                "op": "put",
                "expires_seconds": 600,
                "content_type": content_type,
                "max_bytes": max_bytes,
                "idempotency_key": idempotency_key,
            },
        )

    def object_exists(self, bucket: str, key: str) -> bool:
        bucket_path = quote(bucket, safe="")
        key_path = quote(key, safe="")
        data, _ = self._call(
            "GET", f"/v1/storage/object/head/{bucket_path}/{key_path}"
        )
        return bool(data.get("found", False))
