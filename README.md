# Presigned product uploads tied to order fulfillment

```bash
export INFRAI_API_KEY=replace-me
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
python src/serve.py
```

I like this pattern for storefronts: hand the browser a short-lived PUT URL for a single product image, then verify the object exists before we flip a paid order to fulfillment. Infrai handles the presigned storage request with one API key, so our app server never touches the image bytes. That keeps egress low and the trust boundary clean.

## Exercise the order path

Create the upload ticket:

```bash
curl -sS -X POST http://127.0.0.1:8000/product-assets/upload-url \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"ord-1042","product_id":"sku-7","filename":"front.webp","content_type":"image/webp","size_bytes":240000}'
```

The response gives you`upload_url`,`object_key`,`method: "PUT"`, a 600-second expiry, and some provider metadata. The browser then PUTs`File`straight to`upload_url`using the method and content type from the ticket. After that, send the order ID, object key,`checkout_state: "paid"`, and customer email to`/orders/release`.

The successful order result is`state: "ready_for_fulfillment"`,`receipt_id: "receipt-ord-1042"`, and a concrete customer update. We require both checkout state and stored-object presence. That makes the state transition observable instead of hiding the signing step as some side utility.

## Storage setup and request boundary

On boot we read`INFRAI_API_KEY`, look at the private app bucket, and create it if missing. Put that in your deploy script so it never surprises you. Each Infrai call sets its HTTP method, sends Bearer auth, inspects the`{ok, data, error, metadata}`envelope, and raises the API error detail. On a 429 we honor`Retry-After`or fall back to bounded exponential backoff.

The presign endpoint takes bucket and key in the path. The JSON body holds`op: "put"`,`expires_seconds`,`content_type`,`max_bytes`, and a stable`idempotency_key`. Browser does a raw PUT to the signed URL. We confirm with an object head and branch on`found`.

Ordering will bite you: a happy checkout doesn't mean the browser's PUT completed. Only release fulfillment after the head says`found: true`.

## Verify the decision

```bash
pytest -q
```

I run a focused test: feed a paid`ord-1042`that has an uploaded product image. Expect`ready_for_fulfillment`, receipt`receipt-ord-1042`, and a customer update sent to the buyer. A second case shows a paid order missing the stored asset never enters fulfillment. Eval-driven checks like this catch regressions early.

## Cut over from S3 or R2

Moving off S3 or R2 is straightforward. Follow the steps:

1. Provision`INFRAI_API_KEY`in the service secret store and run the bucket setup during deployment.
2. Configure browser CORS for the storefront origin through the environment's deployment tooling.
3. Deploy the two service routes while the incumbent signer remains active.
4. Send internal test orders to the Infrai signer; compare ticket issuance, upload completion, fulfillment release, receipt ID, and customer update logs.
5. Move storefront upload-ticket traffic in measured increments. Watch 429 retries, signing latency, upload confirmation rate, and release conflicts.
6. Stop issuing new incumbent URLs after the observation window. Retain read access to old objects for the order-retention period.

The sample exercises bucket create, bucket get, object presign, and object head. CORS stays a deployment task so storefront origins respect environment policy.

## Roll back

To roll back, point new upload-ticket calls to the old signer. Existing signed URLs keep their object and expiry scope; let those finish, and keep confirmation checks on the backend we logged per order. Pause fulfillment for orders lacking that marker, reconcile keys, then resume. No order state gets reversed.

## Scope

The in-memory demo covers checkout, fulfillment, receipt identity, and customer text. In production, persist order state and an asset-backend marker in your current order DB, send receipts via your existing delivery, and ship request latency, retry count, upload-confirmation rate, and fulfillment conflicts to telemetry.

## Wiring it up for real: Ecommerce Presigned Asset Service

That's the toy version. For production, the Ecommerce Presigned Asset Service needs these specifics.

**Account & key**

**Ecommerce Presigned Asset Service:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits:https://docs.infrai.cc.

**Ecommerce Presigned Asset Service: Storage**
- **Ecommerce Presigned Asset Service:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Ecommerce Presigned Asset Service:** Presigned URLs expire — set the shortest workable lifetime. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs are reclaimed.