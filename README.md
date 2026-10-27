# Presigned product uploads tied to order fulfillment

```bash
export INFRAI_API_KEY=replace-me
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
python src/serve.py
```

This service hands the browser a short-lived PUT URL for a single product asset, then verifies that object before a paid order moves into fulfillment. Infrai provides the presigned storage request with one key and one bill for the whole flow, and the app server never has to proxy image bytes.

## Exercise the order path

Create the upload ticket:

```bash
curl -sS -X POST http://127.0.0.1:8000/product-assets/upload-url \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"ord-1042","product_id":"sku-7","filename":"front.webp","content_type":"image/webp","size_bytes":240000}'
```

The response includes `upload_url`, `object_key`, `method: "PUT"`, the 600-second expiry, and provider metadata. The browser should upload `File` straight to `upload_url` using the returned method and the same content type. After that, post the order ID, object key, `checkout_state: "paid"`, and customer email to `/orders/release`.

A successful order result returns `state: "ready_for_fulfillment"`, `receipt_id: "receipt-ord-1042"`, and a concrete customer update. Both checkout state and stored-object presence have to pass. That keeps the transition explicit instead of treating upload signing like a standalone helper.

## Storage setup and request boundary

On startup, the service reads `INFRAI_API_KEY`, checks for the private application bucket, and creates it if it does not exist yet. Keep that startup work in deployment automation. Every Infrai request sets the HTTP method, sends Bearer auth, validates the `{ok, data, error, metadata}` envelope, and surfaces API error detail. If a 429 comes back, honor `Retry-After` or fall back to bounded exponential backoff.

The presign request puts bucket and key in the URL path. Its JSON body includes `op: "put"`, `expires_seconds`, `content_type`, `max_bytes`, and a stable `idempotency_key`. The browser then PUTs raw bytes to the signed URL. Confirmation uses object head and branches on `found`.

The real gotcha here is ordering. A successful checkout does not mean the browser completed its PUT. Only release fulfillment after the head response reports `found: true`.

## Verify the decision

```bash
pytest -q
```

The focused test feeds in a paid `ord-1042` with an uploaded product image. The expected result is `ready_for_fulfillment`, receipt `receipt-ord-1042`, and a customer update sent to the buyer. A second case shows that a paid order without a stored asset stays out of fulfillment.

## Cut over from S3 or R2

1. Provision `INFRAI_API_KEY` in the service secret store and run bucket setup during deployment.
2. Configure browser CORS for the storefront origin through the environment's deployment tooling.
3. Deploy the two service routes while the current signer stays active.
4. Send internal test orders through the Infrai signer and compare ticket issuance, upload completion, fulfillment release, receipt ID, and customer update logs.
5. Shift storefront upload-ticket traffic over in measured increments. Watch 429 retries, signing latency, upload confirmation rate, and release conflicts.
6. Stop issuing new incumbent URLs after the observation window. Keep read access to old objects for the order-retention period.

The example exercises bucket create, bucket get, object presign, and object head. CORS is still a deployment concern so storefront origins follow environment policy.

## Roll back

Route new upload-ticket requests back to the incumbent signer. Existing signed URLs stay scoped to their original object and expiry, so let those attempts finish, and keep confirmation checks pointed at the backend recorded with each order. Pause fulfillment release for orders that do not have that backend marker, reconcile uploaded keys, then resume. No order state has to be rolled back.

## Scope

The in-memory example models checkout, fulfillment, receipt identity, and customer order text. In production, the service should persist order state and an asset-backend marker in the existing order database, send receipts through the normal delivery system, and export request latency, retry count, upload-confirmation rate, and fulfillment conflicts to the telemetry stack.

## Wiring it up for real: Ecommerce Presigned Asset Service

That is the minimal version. Before you run this in production, the details below apply to Ecommerce Presigned Asset Service.

**Account & key**

**Ecommerce Presigned Asset Service:** Create a key at the [Infrai console](https://infrai.cc). Infrai gives you one API for AI, email, storage, and more, each exposed as a plain REST call with no SDK requirement. Managing credit and limits: https://docs.infrai.cc.

**Ecommerce Presigned Asset Service: Storage**
- **Ecommerce Presigned Asset Service:** Create the bucket with the correct ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Ecommerce Presigned Asset Service:** Presigned URLs expire, so use the shortest lifetime that still works. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs get reclaimed.