from order_asset_service import (
    ASSET_BUCKET,
    FulfillmentRequest,
    OrderAssetWorkflow,
    OrderState,
    UploadRequest,
)


class RecordingStorage:
    def __init__(self, *, found: bool = True) -> None:
        self.found = found
        self.bucket_names: list[str] = []

    def ensure_bucket(self, name: str) -> None:
        self.bucket_names.append(name)

    def presign_product_put(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        idempotency_key: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return {"url": "https://upload.example/signed"}, {"vendor": "test"}

    def object_exists(self, bucket: str, key: str) -> bool:
        return self.found


def test_paid_order_with_uploaded_asset_enters_fulfillment() -> None:
    storage = RecordingStorage(found=True)
    workflow = OrderAssetWorkflow(storage)
    workflow.prepare()
    ticket = workflow.issue_upload(
        UploadRequest(
            order_id="ord-1042",
            product_id="sku-7",
            filename="front.webp",
            content_type="image/webp",
            size_bytes=240_000,
        )
    )

    update = workflow.release_fulfillment(
        FulfillmentRequest(
            order_id="ord-1042",
            object_key=ticket.object_key,
            checkout_state=OrderState.PAID,
            customer_email="buyer@example.com",
        )
    )

    assert storage.bucket_names == [ASSET_BUCKET]
    assert update.state == OrderState.READY_FOR_FULFILLMENT
    assert update.receipt_id == "receipt-ord-1042"
    assert "buyer@example.com" in update.customer_message


def test_missing_asset_keeps_paid_order_out_of_fulfillment() -> None:
    workflow = OrderAssetWorkflow(RecordingStorage(found=False))
    request = FulfillmentRequest(
        order_id="ord-1042",
        object_key="products/sku-7/orders/ord-1042/front.webp",
        checkout_state=OrderState.PAID,
        customer_email="buyer@example.com",
    )

    try:
        workflow.release_fulfillment(request)
    except ValueError as exc:
        assert str(exc) == "product asset upload is not complete"
    else:
        raise AssertionError("missing asset must block fulfillment")
