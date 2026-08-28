from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from infrai_storage import InfraiStorage


ASSET_BUCKET = "commerce-product-assets"


class OrderState(StrEnum):
    CHECKOUT_PENDING = "checkout_pending"
    PAID = "paid"
    READY_FOR_FULFILLMENT = "ready_for_fulfillment"


class UploadRequest(BaseModel):
    order_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    content_type: str = Field(pattern=r"^image/(jpeg|png|webp)$")
    size_bytes: int = Field(gt=0, le=10_000_000)


class UploadTicket(BaseModel):
    order_id: str
    object_key: str
    upload_url: str
    method: str = "PUT"
    expires_seconds: int = 600
    provider_metadata: dict[str, object]


class FulfillmentRequest(BaseModel):
    order_id: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    checkout_state: OrderState
    customer_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OrderUpdate(BaseModel):
    order_id: str
    state: OrderState
    receipt_id: str
    customer_message: str


class StoragePort(Protocol):
    def ensure_bucket(self, name: str) -> None:
        raise RuntimeError("protocol method")

    def presign_product_put(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        idempotency_key: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        raise RuntimeError("protocol method")

    def object_exists(self, bucket: str, key: str) -> bool:
        raise RuntimeError("protocol method")


class OrderAssetWorkflow:
    def __init__(self, storage: StoragePort) -> None:
        self.storage = storage

    def prepare(self) -> None:
        self.storage.ensure_bucket(ASSET_BUCKET)

    def issue_upload(self, request: UploadRequest) -> UploadTicket:
        key = f"products/{request.product_id}/orders/{request.order_id}/{request.filename}"
        data, metadata = self.storage.presign_product_put(
            ASSET_BUCKET,
            key,
            content_type=request.content_type,
            max_bytes=request.size_bytes,
            idempotency_key=f"asset-upload:{request.order_id}:{request.product_id}:{request.filename}",
        )
        return UploadTicket(
            order_id=request.order_id,
            object_key=key,
            upload_url=str(data["url"]),
            provider_metadata=metadata,
        )

    def release_fulfillment(self, request: FulfillmentRequest) -> OrderUpdate:
        if request.checkout_state != OrderState.PAID:
            raise ValueError("checkout must be paid before fulfillment")
        if not self.storage.object_exists(ASSET_BUCKET, request.object_key):
            raise ValueError("product asset upload is not complete")
        return OrderUpdate(
            order_id=request.order_id,
            state=OrderState.READY_FOR_FULFILLMENT,
            receipt_id=f"receipt-{request.order_id}",
            customer_message=(
                f"Order {request.order_id} is paid and ready for fulfillment; "
                f"receipt sent to {request.customer_email}."
            ),
        )


def create_app(storage: StoragePort | None = None) -> FastAPI:
    workflow = OrderAssetWorkflow(storage or InfraiStorage())
    workflow.prepare()
    api = FastAPI(title="Product asset order service")

    @api.post("/product-assets/upload-url", response_model=UploadTicket)
    def upload_url(request: UploadRequest) -> UploadTicket:
        return workflow.issue_upload(request)

    @api.post("/orders/release", response_model=OrderUpdate)
    def release(request: FulfillmentRequest) -> OrderUpdate:
        try:
            return workflow.release_fulfillment(request)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return api
