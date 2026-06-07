# Spec #24b — Local Frontend–Backend Integration Test

**Project:** Dwarika's Omnichannel Tech Ecosystem  
**Spec Type:** Developer Integration Validation  
**Sequence:** Inserted between Spec #24 (Blinkit/JioMart One-Click Listing) and Spec #25 (Cloud Run Deployment Readiness).  
**Status:** 🔲 Not started

---

## 1. Purpose & Scope

This spec defines a structured, end-to-end integration test phase where the **Dwarikas frontend** (running locally, e.g., `http://localhost:3000`) connects to the **Dwarikas Django backend** (running locally, e.g., `http://localhost:8000`) and exercises every implemented API capability.

The goal is to **catch integration issues before infrastructure work begins** (Spec #25). Problems found here are cheap to fix; problems found post-Cloud-Run are expensive.

### What this spec covers

- Local environment setup for both frontend and backend
- CORS and authentication wiring between the two
- A scenario-based test playbook covering every backend capability documented in `context/backend_capabilities.md`
- Pass/fail acceptance criteria for each scenario
- Defect tracking and resolution workflow

### What this spec does NOT cover

- Production secrets, GCP infrastructure, Cloud Run
- External integrations that require live vendor credentials (Amazon SP-API, Blinkit, JioMart, ONDC, WhatsApp, Razorpay) — these use mock/stub mode only
- Frontend implementation quality (UI/UX design is out of scope here)

---

## 2. Prerequisites

### 2.1 Backend (Local)

| Requirement | Detail |
|---|---|
| Python 3.14 + `uv` | Install via `uv` as documented in Spec #01 |
| Local Supabase stack | Running on `localhost:54321` (Supabase Studio: `localhost:54323`) |
| Local Redis | Running on `localhost:6379` (used for rate limiting / JTI blocklist) |
| `.env` file | Copy `.env.example` → `.env` with local Supabase credentials |
| Django dev server | `python manage.py runserver 0.0.0.0:8000` |

**Minimum `.env` values required for local dev:**

```env
DEBUG=True
DJANGO_SECRET_KEY=local-dev-secret-key-not-for-production
DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres
SUPABASE_JWT_SECRET=<your-local-supabase-jwt-secret>
REDIS_URL=redis://localhost:6379/0
ALLOWED_HOSTS=localhost,127.0.0.1
# Leave the following blank for local dev (mock mode activates automatically)
GCS_BUCKET_NAME=
GCP_PROJECT_ID=
CLOUD_TASKS_QUEUE=
CLOUD_TASKS_REGION=
DOCUMENT_AI_PROCESSOR_ID=
EXTERNAL_SIGNING_SECRET=local-signing-secret-32chars-min
WHATSAPP_VERIFY_TOKEN=local-verify-token
WHATSAPP_API_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
AMAZON_LWA_CLIENT_ID=
AMAZON_LWA_CLIENT_SECRET=
AMAZON_SELLER_ID=
AMAZON_SQS_QUEUE_URL=
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
ONDC_SUBSCRIBER_ID=dwarikas.local
ONDC_PRIVATE_KEY=
FYND_ACCESS_TOKEN=
BLINKIT_VENDOR_ID=
BLINKIT_WEBHOOK_SECRET=
```

> **CORS:** The backend's `settings.py` uses `CORS_ALLOW_ALL_ORIGINS = True` when `CORS_ALLOWED_ORIGINS` env var is not set — this means local dev requires **no CORS changes**. Do not change CORS settings during this spec.

### 2.2 Frontend (Local)

| Requirement | Detail |
|---|---|
| Node.js 20+ | Required by Next.js or Vite |
| Package manager | `npm` or `pnpm` |
| Dev server | `npm run dev` — runs on `http://localhost:3000` (or configured port) |
| Supabase JS client | `@supabase/supabase-js` — used for auth token acquisition |
| Base API URL | `http://localhost:8000` (configured via env var `NEXT_PUBLIC_API_BASE_URL` or equivalent) |

**Minimum frontend `.env.local` values:**

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=http://localhost:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<your-local-supabase-anon-key>
```

### 2.3 Test Users

Before starting the test playbook, create the following users in the local Supabase Auth dashboard (`http://localhost:54323`):

| User | Email | Role (`app_metadata.role`) | Password |
|---|---|---|---|
| Manager | `manager@dwarikas.local` | `manager` | `Test@1234` |
| Staff | `staff@dwarikas.local` | `staff` | `Test@1234` |
| Customer | `customer@dwarikas.local` | `customer` | `Test@1234` |
| Unregistered | _(no account)_ | — | — |

Set `app_metadata` via the Supabase Dashboard → Authentication → Users → Edit User → Custom Claims:
```json
{ "role": "manager" }
```

---

## 3. Integration Test Playbook

Each scenario below is a structured test case. A **frontend developer** should implement a minimal UI flow (or use a REST client like the browser fetch API / Postman-style tool in the frontend console) to exercise each endpoint.

The **backend developer** monitors Django's dev server logs to confirm correct handling.

---

### Module 1: Authentication & Health

---

#### T-01 — Health Check (No Auth)

**Endpoint:** `GET /api/v1/health/`  
**Auth:** None  
**Frontend action:** Load the app → frontend calls health check on startup.

**Expected result:**
```json
{ "status": "ok", "database": "ok", "cache": "ok" }
```
**HTTP status:** `200 OK`

**Pass criteria:** Frontend receives `status: "ok"`. App displays "Backend connected" indicator.

---

#### T-02 — Login (Manager)

**Endpoint:** Supabase Auth (not backend)  
**Frontend action:** Login form → submit `manager@dwarikas.local` / `Test@1234`.

**Expected result:** Supabase JS client returns session with JWT access token.

**Pass criteria:**  
- Token is stored (memory or `localStorage` / `sessionStorage`)
- Token contains `app_metadata.role = "manager"`
- Frontend routes to manager dashboard

---

#### T-03 — Login (Staff)

Same as T-02 but for `staff@dwarikas.local`.  
**Pass criteria:** Token contains `app_metadata.role = "staff"`. Frontend routes to staff dashboard (or manager dashboard with staff-level access).

---

#### T-04 — Login (Customer)

Same as T-02 but for `customer@dwarikas.local`.  
**Pass criteria:** Token contains `app_metadata.role = "customer"`. Frontend routes to customer storefront.

---

#### T-05 — Logout & Token Revocation

**Endpoint:** `POST /api/v1/auth/logout/`  
**Auth:** Manager JWT  
**Frontend action:** Click "Logout" button.

**Expected result:**
```json
{ "detail": "Logged out successfully." }
```
**HTTP status:** `200 OK`

**Pass criteria:**  
- Backend adds JTI to blocklist
- Frontend clears stored token
- Subsequent requests with the old token return `401 Unauthorized`

---

#### T-06 — Unauthenticated Access to Protected Endpoint

**Endpoint:** `POST /api/v1/orders/confirm/` (no Authorization header)  
**Frontend action:** Attempt to confirm order while logged out.

**Expected result:** `401 Unauthorized`

**Pass criteria:** Frontend displays "Please log in to continue."

---

### Module 2: Product Catalog (Public Read)

---

#### T-07 — List All Products (Anonymous)

**Endpoint:** `GET /api/v1/products/`  
**Auth:** None  
**Frontend action:** Visit storefront product listing page (not logged in).

**Expected result:** Paginated product list, `200 OK`

**Pass criteria:**  
- Products render correctly with name, price, variants
- Pagination controls work (`?page=2` etc.)
- No auth error

---

#### T-08 — Filter Products by Category

**Endpoint:** `GET /api/v1/products/?category=rice`  
**Frontend action:** Use category filter / search in storefront.

**Pass criteria:** Only products matching category filter appear.

---

#### T-09 — Search Products

**Endpoint:** `GET /api/v1/products/?search=basmati`  
**Frontend action:** Use the search box.

**Pass criteria:** Matching products appear, irrelevant products are excluded.

---

#### T-10 — Product Detail

**Endpoint:** `GET /api/v1/products/<uuid>/`  
**Frontend action:** Click on a product card.

**Pass criteria:** Full product detail renders including nested variants array.

---

#### T-11 — Create Product (Manager)

**Endpoint:** `POST /api/v1/products/`  
**Auth:** Manager JWT  
**Frontend action:** Manager navigates to "Add Product" form, fills it in, submits.

**Request body:**
```json
{
  "name": "Integration Test Rice",
  "hsn_code": "1006",
  "gst_slab": "5.00",
  "product_type": "grocery",
  "brand": "Test Brand",
  "category": "Grains",
  "dietary_type": "veg"
}
```

**Pass criteria:** `201 Created`, product appears in listing.

---

#### T-12 — Create Product (Staff — should succeed)

Same as T-11 but with Staff JWT.  
**Pass criteria:** `201 Created` (staff has write access to products per `IsStaffOrManager`).

---

#### T-13 — Create Product (Customer — should be rejected)

Same as T-11 but with Customer JWT.  
**Pass criteria:** `403 Forbidden`. Frontend shows "Insufficient permissions."

---

#### T-14 — Create Product Variant

**Endpoint:** `POST /api/v1/products/<uuid>/variants/`  
**Auth:** Manager JWT  
**Frontend action:** Manager opens product, clicks "Add Variant".

**Request body:**
```json
{
  "sku": "INTTEST-RICE-1KG",
  "barcode": "9999999999001",
  "size": "1kg",
  "stock_quantity": 100,
  "retail_price": "120.00",
  "mrp": "130.00",
  "unit_of_measure": "kg"
}
```

**Pass criteria:** `201 Created`. Variant appears under product.

---

#### T-15 — Delete Product (Manager only)

**Endpoint:** `DELETE /api/v1/products/<uuid>/`  
**Auth:** Staff JWT → expected `403`. Manager JWT → expected `204`.  
**Frontend action:** Delete button on product card (test both roles).

**Pass criteria:**  
- Staff gets `403 Forbidden`
- Manager gets `204 No Content` and product disappears from listing

---

### Module 3: Checkout & Reservations

---

#### T-16 — Create Reservation (Customer)

**Endpoint:** `POST /api/v1/checkout/reserve/`  
**Auth:** Customer JWT  
**Frontend action:** Customer clicks "Add to Cart" / "Reserve" on a variant.

**Request body:**
```json
{
  "variant_id": "<uuid of INTTEST-RICE-1KG>",
  "quantity": 2
}
```

**Pass criteria:** `201 Created` with `reservation_id`, `expires_at` (~10 min from now), `status: "active"`.

---

#### T-17 — List Active Reservations

**Endpoint:** `GET /api/v1/checkout/reserve/list/`  
**Auth:** Customer JWT  
**Frontend action:** View cart / basket page.

**Pass criteria:** Reservation from T-16 appears with full nested variant details.

---

#### T-18 — Reservation Conflict (Oversell Prevention)

**Endpoint:** `POST /api/v1/checkout/reserve/`  
**Auth:** Customer JWT  
**Frontend action:** Attempt to reserve more than available ATP.

> Prerequisite: Set `stock_quantity = 2` on the test variant. Reserve 2 units in T-16. Then attempt to reserve 1 more unit with another customer account.

**Pass criteria:** `409 Conflict` with `atp: 0`, `requested: 1`. Frontend shows "Out of stock."

---

#### T-19 — Release Reservation

**Endpoint:** `DELETE /api/v1/checkout/reserve/<reservation_id>/`  
**Auth:** Customer JWT (own reservation)  
**Frontend action:** Remove item from cart.

**Pass criteria:** `204 No Content`. Reservation no longer in listing. ATP is restored.

---

### Module 4: Orders

---

#### T-20 — Confirm Order

**Endpoint:** `POST /api/v1/orders/confirm/`  
**Auth:** Customer JWT  
**Frontend action:** Click "Confirm & Pay" after reserving.

**Request body:**
```json
{
  "reservation_id": "<uuid>",
  "payment_method": "UPI"
}
```

**Pass criteria:**  
- `201 Created` with `order_id`, `total_amount`, `gst_amount`, `payment_status: "completed"`
- Stock is decremented in the product variant

---

#### T-21 — List Orders (Customer sees only own)

**Endpoint:** `GET /api/v1/orders/`  
**Auth:** Customer JWT  
**Frontend action:** Visit "My Orders" page.

**Pass criteria:** Only orders belonging to the logged-in customer appear.

---

#### T-22 — List Orders (Staff sees all)

**Endpoint:** `GET /api/v1/orders/`  
**Auth:** Staff JWT  
**Frontend action:** Staff visits order management panel.

**Pass criteria:** All orders (from all users) appear in the list.

---

#### T-23 — Order Detail

**Endpoint:** `GET /api/v1/orders/<uuid>/`  
**Auth:** Customer JWT (own order)  
**Frontend action:** Click order row in "My Orders".

**Pass criteria:** Full order object renders correctly.

---

#### T-24 — Expired Reservation Attempt (410 Gone)

**Endpoint:** `POST /api/v1/orders/confirm/`  
**Auth:** Customer JWT  
**Setup:** Manually set `expires_at` to a past timestamp in the local DB for a test reservation.  
**Frontend action:** Attempt to confirm the expired reservation.

**Pass criteria:** `410 Gone`. Frontend shows "Your cart expired, please start over."

---

### Module 5: Barcode Generation

---

#### T-25 — Generate Code 128 Barcode

**Endpoint:** `GET /api/v1/barcodes/INTTEST-RICE-1KG/code128/`  
**Auth:** Staff JWT  
**Frontend action:** Staff clicks "Print Barcode" on a variant.

**Pass criteria:**  
- Response `Content-Type: image/png`
- Image renders in an `<img>` tag
- No authentication error

---

#### T-26 — Generate EAN-13 Barcode (Valid 12-digit SKU)

**Endpoint:** `GET /api/v1/barcodes/123456789012/ean13/`  
**Auth:** Staff JWT  
**Setup:** Create a variant with SKU `123456789012` (12 digits).  
**Frontend action:** Print barcode for EAN-13 SKU.

**Pass criteria:** `200 OK`, PNG image returned.

---

#### T-27 — Generate EAN-13 Barcode (Invalid SKU)

**Endpoint:** `GET /api/v1/barcodes/INTTEST-RICE-1KG/ean13/`  
**Auth:** Staff JWT

**Pass criteria:** `422 Unprocessable Entity`. Frontend shows "This SKU cannot be used for EAN-13."

---

#### T-28 — Barcode as Customer (Rejected)

**Endpoint:** `GET /api/v1/barcodes/INTTEST-RICE-1KG/code128/`  
**Auth:** Customer JWT

**Pass criteria:** `403 Forbidden`. Frontend hides "Print Barcode" button for customers.

---

### Module 6: Loose Product Repackaging

---

#### T-29 — Create Packaging Job

**Endpoint:** `POST /api/v1/packaging-jobs/`  
**Auth:** Staff JWT  
**Frontend action:** Staff opens "Repackaging" screen, fills in details.

**Request body:**
```json
{
  "source_description": "Integration Test 50kg Basmati",
  "bulk_quantity_used": "50.000",
  "bulk_unit": "kg",
  "notes": "Integration test run",
  "outputs": [
    {
      "product_id": "<uuid>",
      "sku": "INTTEST-RICE-1KG-PACK",
      "weight_per_packet": "1.000",
      "unit_of_measure": "kg",
      "packets_produced": 48,
      "retail_price": "120.00"
    }
  ]
}
```

**Pass criteria:**  
- `201 Created`
- Response includes `outputs[0].barcode_image_url` with a valid URL
- `stock_quantity` for the output variant is incremented by 48
- `is_new_variant: true` if the SKU didn't exist before

---

#### T-30 — List Packaging Jobs

**Endpoint:** `GET /api/v1/packaging-jobs/`  
**Auth:** Staff JWT  
**Frontend action:** View repackaging history.

**Pass criteria:** Packaging job from T-29 appears in the paginated list.

---

### Module 7: Invoice Ingestion & HITL Workflow

> **Note:** GCS and Document AI are not available in local dev. The backend uses a **local filesystem fallback** for file storage and a **mock OCR parser** that returns synthetic line items. This is sufficient to test the full workflow end-to-end.

---

#### T-31 — Upload Invoice (PDF)

**Endpoint:** `POST /api/v1/invoices/upload/`  
**Auth:** Staff JWT  
**Content-Type:** `multipart/form-data`  
**Frontend action:** Staff clicks "Upload Invoice", selects a sample PDF from their machine.

**Pass criteria:**  
- `202 Accepted` with `invoice_id` and `status: "processing"`
- Django logs show Cloud Tasks simulation thread started

---

#### T-32 — Poll Invoice Status

**Endpoint:** `GET /api/v1/invoices/<invoice_id>/`  
**Auth:** Staff JWT  
**Frontend action:** Frontend polls every 3 seconds after upload.

**Pass criteria:**  
- Status transitions from `processing` → `review` (after mock OCR completes, ~5–10 seconds)
- Response includes `line_items` array with mock-extracted items

---

#### T-33 — Correct Line Item (HITL)

**Endpoint:** `PATCH /api/v1/invoices/<invoice_id>/line-items/<line_item_id>/`  
**Auth:** Staff JWT  
**Frontend action:** Staff edits a line item in the invoice review UI.

**Request body:**
```json
{
  "sku": "INTTEST-RICE-1KG",
  "quantity": 10,
  "unit_price": "110.00"
}
```

**Pass criteria:** `200 OK`. `needs_review: false` on the updated line item.

---

#### T-34 — Confirm Invoice (Stock Addition)

**Endpoint:** `POST /api/v1/invoices/<invoice_id>/confirm/`  
**Auth:** Manager JWT  
**Frontend action:** Manager clicks "Confirm Invoice" after review.

**Pass criteria:**  
- `200 OK`
- Invoice `status` changes to `confirmed`
- `stock_quantity` on matched SKU variants is incremented by confirmed quantities
- Re-confirm returns `409 Conflict` (idempotency guard)

---

### Module 8: Security & Rate Limiting

---

#### T-35 — Rate Limiting (Anonymous)

**Frontend action:** Fire 65 rapid GET requests to `GET /api/v1/products/` with no auth token.

**Pass criteria:** After 60 requests within 60 seconds, backend returns `429 Too Many Requests`.

---

#### T-36 — Rate Limiting (Authenticated)

Same as T-35 but with Customer JWT. Limit is 300/min.  
**Pass criteria:** First 300 requests succeed; 301st returns `429`.

---

#### T-37 — Brute Force Protection

**Frontend action:** Attempt to log in with wrong password 6 times from the same IP.  
**Note:** `django-axes` tracks failed attempts. Trigger via repeated bad password submissions on the login form.

**Pass criteria:** After 5 failed attempts, login response returns `403 Forbidden` with `{"detail": "Access Locked"}`.

---

#### T-38 — External Partner API Key Authentication

**Endpoint:** `GET /api/v1/external/inventory/sync/` (or relevant external endpoint)  
**Auth:** `X-Dwarikas-Api-Key: <key>` (generated via `python manage.py create_api_key`)  
**Frontend action:** (Not a browser flow — test via `curl` or backend script)

**Pass criteria:** `200 OK` with valid key. `401` with invalid key.

---

### Module 9: WhatsApp Commerce Engine (Mock)

> WhatsApp Cloud API calls are mocked in local dev (no real messages sent). The webhook handler can still be tested end-to-end by sending simulated payloads.

---

#### T-39 — WhatsApp Webhook Verification

**Endpoint:** `GET /api/v1/whatsapp/webhook/?hub.mode=subscribe&hub.verify_token=<WHATSAPP_VERIFY_TOKEN>&hub.challenge=test123`  
**Auth:** None  
**Frontend action:** (Not a browser flow) — simulate the Meta webhook handshake via `curl` or frontend dev tool.

**Pass criteria:** Response body is `test123` (the challenge echo), `200 OK`.

---

#### T-40 — WhatsApp Catalog Intent

**Endpoint:** `POST /api/v1/whatsapp/webhook/`  
**Frontend action:** Simulate a WhatsApp message payload containing the word "catalog" via the dev console or backend test script.

**Pass criteria:** Django logs show catalog list was fetched and (mocked) response would be sent.

---

### Module 10: ONDC Seller Node (Mock Signature)

> ONDC integration requires Ed25519 signing. In local dev, signature verification can be tested using a locally-generated Ed25519 key pair.

---

#### T-41 — ONDC Search Endpoint

**Endpoint:** `POST /api/v1/ondc/search/`  
**Auth:** Valid Ed25519-signed Beckn `Authorization` header  
**Frontend action:** (Not a browser flow) — developer uses a test script to construct a signed ONDC search request.

**Pass criteria:**  
- Signature verification passes
- Backend returns a Beckn-formatted catalog response
- Django logs show `on_search` callback was queued

---

### Module 11: Payment Gateway (Razorpay Mock)

> Razorpay live credentials are not available in local dev. Use Razorpay's **test mode keys** (`rzp_test_*`). No real money is charged.

---

#### T-42 — Create Razorpay Order

**Endpoint:** `POST /api/v1/payments/razorpay/create-order/`  
**Auth:** Customer JWT  
**Frontend action:** Customer proceeds to payment, frontend calls this endpoint.

**Pass criteria:** `201 Created` with Razorpay `order_id`. Frontend opens Razorpay checkout modal.

---

#### T-43 — Razorpay Webhook Simulation

**Endpoint:** `POST /api/v1/payments/razorpay/webhook/`  
**Auth:** `X-Razorpay-Signature` header (HMAC-SHA256 of payload using `RAZORPAY_WEBHOOK_SECRET`)

**Pass criteria:**  
- `200 OK` on valid signature
- Payment record updated accordingly
- `400` on tampered/invalid signature

---

### Module 12: Omnichannel Listing (Stub Mode)

> Amazon, Blinkit, and JioMart live credentials are not available in local dev. The backend stubs return mock responses.

---

#### T-44 — Amazon Product Listing (Stub)

**Endpoint:** `POST /api/v1/amazon/listings/`  
**Auth:** Manager JWT  
**Frontend action:** Manager clicks "List on Amazon" from product detail page.

**Pass criteria:** `202 Accepted` with a stub response (mock listing submission ID). No real API call is made.

---

#### T-45 — Blinkit/JioMart Listing (Stub)

**Endpoint:** `POST /api/v1/quickcommerce/listings/`  
**Auth:** Manager JWT  
**Frontend action:** Manager clicks "List on Blinkit/JioMart" from product detail page.

**Pass criteria:** `202 Accepted` with a stub response. No real API call is made.

---

## 4. CORS Validation

This section specifically validates that the frontend origin is accepted by the backend.

### C-01 — Cross-Origin Preflight (OPTIONS)

**Frontend action:** Any state-changing API call (POST, PATCH, DELETE) from `http://localhost:3000` will trigger a CORS preflight.

**Expected:** `200 OK` preflight response with:
```
Access-Control-Allow-Origin: http://localhost:3000
Access-Control-Allow-Methods: GET, POST, PATCH, PUT, DELETE, OPTIONS
Access-Control-Allow-Headers: Authorization, Content-Type
```

**Pass criteria:** No CORS errors appear in the browser console.

### C-02 — Credential Headers Pass Through

**Frontend action:** Any authenticated request that sends `Authorization: Bearer <token>`.

**Pass criteria:**
- No `CORS policy: Request header 'authorization' not allowed` error
- Token is received and decoded by the backend

---

## 5. Error Handling Validation

The frontend must gracefully handle all standard error formats:

| HTTP Status | Scenario | Expected Frontend Behavior |
|---|---|---|
| `400 Bad Request` | Validation errors | Show field-level error messages from response body |
| `401 Unauthorized` | Expired/invalid token | Redirect to login page, clear token |
| `403 Forbidden` | Role-based denial | Show "You don't have permission for this action." |
| `404 Not Found` | Missing resource | Show "Resource not found" toast/message |
| `409 Conflict` | Stock conflict | Show remaining ATP, offer alternative products |
| `410 Gone` | Expired reservation | Show "Cart expired" message, redirect to catalog |
| `413 Payload Too Large` | Invoice too large | Show "File too large (max 20MB)" |
| `415 Unsupported Media` | Wrong file type | Show "Only PDF, JPEG, PNG, WebP are accepted" |
| `422 Unprocessable` | Invalid EAN-13 | Show specific validation message |
| `429 Too Many Requests` | Rate limited | Show "Too many requests, please wait" with retry timer |
| `503 Service Unavailable` | Lock contention | Implement exponential backoff (show retry spinner) |

---

## 6. Acceptance Criteria

This spec is **complete** when all of the following are true:

| # | Criterion |
|---|---|
| AC-1 | All T-01 through T-45 test scenarios have been executed and passed |
| AC-2 | Zero CORS errors appear in browser DevTools for any API call |
| AC-3 | All 13 error status codes in Section 5 are rendered correctly by the frontend |
| AC-4 | Token revocation (T-05) is verified: revoked token returns `401` on subsequent use |
| AC-5 | Stock atomicity is verified: T-18 oversell prevention returns `409` |
| AC-6 | Invoice HITL workflow (T-31 → T-34) completes end-to-end using mock OCR |
| AC-7 | All manager-only operations (T-15, T-34) are rejected for staff/customer roles |
| AC-8 | Health check (T-01) is called on frontend startup and result is surfaced to the user |

---

## 7. Defect Tracking

Log any failures found during the playbook using this format:

| ID | Test Scenario | Failure Description | Root Cause | Fix Applied | Verified |
|---|---|---|---|---|---|
| BUG-001 | | | | | |

> **Workflow:** Backend defects → fix in Django code → re-run affected test scenario. Frontend defects → fix in frontend code → re-run. CORS defects → check `settings.py` CORS config and frontend base URL config.

---

## 8. Local Dev Environment Validation Checklist

Before starting the playbook, verify:

| Check | Command / Action | Expected |
|---|---|---|
| Backend running | `curl http://localhost:8000/api/v1/health/` | `{"status": "ok", ...}` |
| Frontend running | Open `http://localhost:3000` in browser | App loads without console errors |
| Supabase running | `supabase status` | All services `HEALTHY` |
| Redis running | `redis-cli ping` | `PONG` |
| Test users created | Supabase Dashboard → Auth → Users | 3 users listed |
| Test product exists | `GET /api/v1/products/` | At least 1 product returned |
| CORS headers present | Browser DevTools → Network → preflight | `Access-Control-Allow-Origin` header present |

---

## 9. Relationship to Other Specs

| Spec | Relationship |
|---|---|
| Spec #03 — Authentication | T-02 through T-06 validate JWT flow |
| Spec #05 — RBAC | T-12, T-13, T-15, T-28 validate role enforcement |
| Spec #06 — Product Catalog | T-07 through T-15 validate full CRUD |
| Spec #07 — Checkout Reservation | T-16 through T-19 validate pessimistic locking |
| Spec #08 — Order Confirmation | T-20 through T-24 validate atomic stock decrement |
| Spec #09 — Barcode Generation | T-25 through T-28 validate image endpoints |
| Spec #09b — Repackaging | T-29, T-30 validate packaging job workflow |
| Spec #10, #11, #12 — Invoice | T-31 through T-34 validate full invoice pipeline |
| Spec #14 — WhatsApp | T-39, T-40 validate webhook handler |
| Spec #15 — External API Gateway | T-38 validates API key auth |
| Spec #16 — Security | T-35 through T-37 validate rate limiting & brute force |
| Spec #17 — Razorpay | T-42, T-43 validate payment gateway integration |
| Spec #23 — Amazon SP-API | T-44 validates stub mode |
| Spec #24 — Blinkit/JioMart | T-45 validates stub mode |
| **Spec #25 — Cloud Run** | This spec is the gate before Spec #25 |
