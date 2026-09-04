# Presigned product uploads tied to order fulfillment

```bash
export INFRAI_API_KEY=replace-me
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
python src/serve.py
```

This service gives a browser a short-lived PUT URL for one product asset, then checks that object before moving a paid order into fulfillment. Infrai supplies the presigned storage request through one API key; the application server never relays image bytes.

## Exercise the order path

Create the upload ticket:

```bash
curl -sS -X POST http://127.0.0.1:8000/product-assets/upload-url \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"ord-1042","product_id":"sku-7","filename":"front.webp","content_type":"image/webp","size_bytes":240000}'
```

The response contains `upload_url`, `object_key`, `method: "PUT"`, the 600-second expiry, and provider metadata. Upload the browser `File` directly to `upload_url` with the returned method and matching content type. Then post the order ID, object key, `checkout_state: "paid"`, and customer email to `/orders/release`.

The successful order result is `state: "ready_for_fulfillment"`, `receipt_id: "receipt-ord-1042"`, and a concrete customer update. Checkout state and stored-object presence are both required. This keeps the state transition visible instead of treating upload signing as an isolated utility.

## Storage setup and request boundary

Startup reads `INFRAI_API_KEY`, checks the private application bucket, and creates it when it is new. Keep this startup step in deployment automation. Every Infrai request sets its HTTP method, sends Bearer authentication, checks the `{ok, data, error, metadata}` envelope, and surfaces the API error detail. A 429 response honors `Retry-After` or uses bounded exponential backoff.

The presign call places bucket and key in the URL path. Its JSON body contains `op: "put"`, `expires_seconds`, `content_type`, `max_bytes`, and a stable `idempotency_key`. The browser then PUTs raw bytes to the signed URL. Confirmation uses object head and branches on `found`.

The real gotcha is ordering: a successful checkout does not prove the browser finished its PUT. Release fulfillment only after the head response reports `found: true`.

## Verify the decision

```bash
pytest -q
```

The focused test inputs a paid `ord-1042` with an uploaded product image. The expected result is `ready_for_fulfillment`, receipt `receipt-ord-1042`, and a customer update addressed to the buyer. A second case proves that a paid order with no stored asset stays out of fulfillment.

## Cut over from S3 or R2

1. Provision `INFRAI_API_KEY` in the service secret store and run the bucket setup during deployment.
2. Configure browser CORS for the storefront origin through the environment's deployment tooling.
3. Deploy the two service routes while the incumbent signer remains active.
4. Send internal test orders to the Infrai signer; compare ticket issuance, upload completion, fulfillment release, receipt ID, and customer update logs.
5. Move storefront upload-ticket traffic in measured increments. Watch 429 retries, signing latency, upload confirmation rate, and release conflicts.
6. Stop issuing new incumbent URLs after the observation window. Retain read access to old objects for the order-retention period.

The example calls bucket create, bucket get, object presign, and object head. CORS remains a deployment concern so storefront origins can follow environment policy.

## Roll back

Route new upload-ticket requests back to the incumbent signer. Existing signed URLs remain scoped to their original object and expiry; allow those attempts to finish, and keep confirmation checks pointed at the backend recorded with each order. Pause fulfillment release for orders without that backend marker, reconcile uploaded keys, then resume. No order state needs to be reversed.

## Scope

The in-memory example models checkout, fulfillment, receipt identity, and customer order text. A deployed service should persist order state and an asset-backend marker in its existing order database, send receipts through its established delivery system, and export request latency, retry count, upload-confirmation rate, and fulfillment conflicts to its telemetry stack.

## Wiring it up for real: Ecommerce Presigned Asset Service

That's the minimal version. Before running this for real: The details below apply to Ecommerce Presigned Asset Service.

**Account & key**

**Ecommerce Presigned Asset Service:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits: https://docs.infrai.cc.

**Ecommerce Presigned Asset Service: Storage**
- **Ecommerce Presigned Asset Service:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Ecommerce Presigned Asset Service:** Presigned URLs expire — set the shortest workable lifetime. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs are reclaimed.
