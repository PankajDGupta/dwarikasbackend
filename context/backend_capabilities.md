# Dwarikas Backend

## Overview

The Dwarikas Backend is a stateless Django REST Framework (DRF) API deployed on Google Cloud Run, serving as the central transaction-integrity and orchestration hub for the Dwarikas omnichannel retail ecosystem. It connects to a Supabase-managed PostgreSQL database and authenticates all requests via locally-decoded Supabase JWTs (HS256, no network round-trip). The backend is built for three user personas: **customers** (shopping via web/mobile), **floor staff** (inventory operations, barcode printing, invoice uploads), and **managers** (full administrative access).

**Tech Stack:** Python 3.14 · Django 6.0.5 · Django REST Framework · Supabase PostgreSQL · Google Cloud Run · Google Cloud Storage · Google Cloud Tasks · Document AI

**Base URL:** `https://<host>/api/v1/`

**All timestamps:** UTC, ISO 8601 format.

---

## Goals

1. **Sub-300ms API response time** for customer checkout validations and in-store POS inventory checks.
2. **85% reduction in manual inventory logging** by parsing uploaded vendor invoices with >95% line-item extraction accuracy using Google Cloud Tasks and Document AI.
3. **100% prevention of concurrent overselling** ("ghost inventory sales") across digital and physical sales counters using atomic PostgreSQL transaction boundaries.

---

## Capabilities for frontend developers

> **This section is the authoritative reference for any agent or developer building a frontend client (Next.js web portal, Admin POS UI, mobile app).** It documents every implemented API endpoint, authentication contract, data model, error format, and pagination behaviour.

---

### 1. Authentication

#### Mechanism

Stateless Supabase JWT validation. The backend **never** calls Supabase Auth servers — tokens are verified locally using the shared HS256 secret.

#### How to Authenticate

1. Obtain a JWT from Supabase Auth (login, signup, magic link, OAuth).
2. Send it as a Bearer token in every request:

```
Authorization: Bearer <SUPABASE_JWT>
```

#### JWT Claims Used by the Backend

| Claim | Location in JWT | Usage |
|---|---|---|
| `sub` | Root | Supabase user UUID — becomes `request.user.username` and `user_id` on orders/reservations |
| `email` | Root | Mapped to `request.user.email` |
| `app_metadata.role` | `app_metadata` object | RBAC role: `customer`, `staff`, or `manager` (defaults to `customer` if absent) |
| `aud` | Root | Must be `"authenticated"` (Supabase default) |

#### Authentication Error Responses

| Status | Condition |
|---|---|
| `401 Unauthorized` | No token, expired token, invalid signature, bad audience, or missing `sub` claim |
| _(no error)_ | No `Authorization` header on public endpoints — request proceeds as anonymous |

---

### 2. Role-Based Access Control (RBAC)

Three roles exist, derived from `app_metadata.role` in the JWT:

| Role | Description | Hierarchy |
|---|---|---|
| `customer` | Default role for all end-users | Lowest |
| `staff` | Floor staff, warehouse workers | Middle |
| `manager` | Store owners, administrators | Highest |

#### Permission Classes (applied per-endpoint)

| Permission Class | Grants Access To |
|---|---|
| `AllowAny` | Everyone (including unauthenticated users) |
| `IsAuthenticated` | Any logged-in user (customer, staff, or manager) |
| `IsStaffOrManager` | `staff` or `manager` roles only |
| `IsManager` | `manager` role only |
| `IsOwnerOrStaff` | Object owner (by `user_id`) OR `staff`/`manager` |

---

### 3. Pagination & Filtering

#### Pagination

All list endpoints return paginated responses using DRF's `PageNumberPagination`:

```json
{
  "count": 142,
  "next": "https://<host>/api/v1/products/?page=2",
  "previous": null,
  "results": [ ... ]
}
```

| Parameter | Default | Description |
|---|---|---|
| `page` | `1` | Page number (1-indexed) |
| Page size | `25` | Fixed at 25 items per page |

#### Global Filter / Search / Ordering Backends

All list views support these query-parameter backends:

- **`DjangoFilterBackend`** — exact/partial field filters via query params (see per-endpoint tables below)
- **`SearchFilter`** — full-text search via `?search=<term>` across configured fields
- **`OrderingFilter`** — sort via `?ordering=<field>` (prefix with `-` for descending)

---

### 4. Data Models & Schemas

#### 4.1 Product

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | Auto | Primary key |
| `name` | string | Yes | Product name |
| `hsn_code` | string | Yes | HSN tax classification code |
| `gst_slab` | decimal(5,2) | Yes (default: 18.00) | GST percentage rate |
| `is_loose_commodity` | boolean | No (default: false) | Whether this product can be repackaged from bulk |
| `product_type` | enum | No (default: `general`) | `grocery`, `apparel`, or `general` |
| `brand` | string | No | Brand name |
| `category` | string | No | Product category |
| `subcategory` | string | No | Product subcategory |
| `description` | string | No | Product description |
| `image_url` | string | No | URL to product image |
| `dietary_type` | enum | No (default: `none`) | `veg`, `non-veg`, `egg`, `none` — grocery only |
| `material` | string | No | Fabric composition — apparel only |
| `gender_target` | enum | No (default: `none`) | `men`, `women`, `unisex`, `boys`, `girls`, `none` — apparel only |
| `fit_type` | string | No | Garment fit (e.g., `Slim Fit`) — apparel only |
| `created_at` | datetime | Auto | ISO 8601 creation timestamp |

> **Frontend note:** Read `product_type` to decide which category-specific fields to render. For `grocery` products, show `dietary_type`. For `apparel` products, show `material`, `gender_target`, `fit_type`.

#### 4.2 ProductVariant

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | UUID | Auto | Primary key |
| `product_id` | UUID (FK) | Yes | Parent product reference |
| `sku` | string | Yes (unique) | Stock-keeping unit identifier |
| `barcode` | string | No (unique) | Barcode value for shelf labels |
| `size` | string | No | Size label (e.g., `M`, `500ml`) |
| `color` | string | No | Color label |
| `stock_quantity` | integer | Yes (default: 0) | Current physical stock count |
| `retail_price` | decimal(12,2) | Yes | Selling price per unit |
| `mrp` | decimal(12,2) | No | Maximum retail price |
| `weight_volume` | string | No | Weight/volume display string |
| `net_quantity` | decimal(10,2) | No | Net quantity value |
| `net_weight_value` | decimal(10,3) | No | Net weight for repackaged items |
| `unit_of_measure` | enum | No (default: `unit`) | `unit`, `kg`, `g`, `litre`, `ml`, `L`, `pcs`, `pack` |
| `created_at` | datetime | Auto | ISO 8601 creation timestamp |

#### 4.3 Reservation

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `variant` | nested object | Full ProductVariant object (on reads) |
| `user_id` | UUID | Supabase user who created the reservation |
| `reserved_quantity` | integer | Number of units held |
| `expires_at` | datetime | 10-minute expiry timestamp (UTC) |
| `status` | enum | `active`, `completed`, `expired` |

#### 4.4 Order

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `user_id` | UUID | Supabase user (nullable for guest checkout) |
| `total_amount` | decimal(12,2) | Subtotal + GST |
| `gst_amount` | decimal(12,2) | Computed GST amount |
| `payment_method` | enum | `UPI`, `card`, `cash` |
| `payment_status` | enum | `pending`, `completed`, `failed` |
| `created_at` | datetime | ISO 8601 creation timestamp |

#### 4.5 PackagingJob

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `source_description` | string | Free-text description of bulk input (e.g., `"50kg Basmati Rice — INV-001"`) |
| `source_variant_id` | UUID (nullable) | Optional link to bulk ProductVariant consumed |
| `bulk_quantity_used` | decimal(10,3) | Total bulk quantity consumed |
| `bulk_unit` | string | Unit of bulk measurement (`kg`, `g`, `litre`, `ml`, `unit`) |
| `notes` | string | Free-text notes |
| `created_by` | UUID | Supabase user who created the job |
| `created_at` | datetime | ISO 8601 creation timestamp |
| `outputs` | array | Nested list of `PackagingJobOutput` objects |

#### 4.6 PackagingJobOutput

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `variant_id` | UUID | The ProductVariant for this packet size |
| `sku` | string | SKU of the output variant |
| `packets_produced` | integer | Number of packets produced |
| `weight_per_packet` | decimal(10,3) | Net weight per packet |
| `unit_of_measure` | string | Unit of measurement |
| `barcode_value` | string | Barcode string for the packet |
| `barcode_image_url` | string | Full URL to the Code 128 barcode image endpoint |
| `retail_price` | decimal(12,2) | Retail price per packet |
| `is_new_variant` | boolean | Whether a new ProductVariant was auto-created |

#### 4.7 PurchaseInvoice

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `invoice_number` | string | Vendor invoice number (populated after OCR) |
| `vendor_name` | string | Vendor name (populated after OCR) |
| `vendor_gstin` | string | Vendor GSTIN (populated after OCR) |
| `issued_at` | date | Invoice issue date (populated after OCR) |
| `gcs_object_path` | string | GCS storage path for the uploaded file |
| `status` | enum | `pending` → `processing` → `review` → `confirmed` (or `failed`) |
| `uploaded_by` | UUID | Supabase user who uploaded the invoice |
| `created_at` | datetime | ISO 8601 creation timestamp |
| `line_items` | array | Nested list of `InvoiceLineItem` objects |

#### 4.8 InvoiceLineItem

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `sku` | string | Extracted SKU (populated after OCR) |
| `description` | string | Line item description |
| `quantity` | integer | Extracted quantity |
| `unit_price` | decimal(12,2) | Per-unit cost |
| `gst_rate` | decimal(5,2) | Extracted GST rate |
| `confidence_score` | decimal(4,3) | OCR confidence (0.000–1.000) |
| `needs_review` | boolean | Flagged for human review if low confidence |

---

### 5. API Endpoints

#### 5.1 Product Catalog (Spec #06)

##### `GET /api/v1/products/` — List Products

| Property | Value |
|---|---|
| **Auth** | `AllowAny` (public) |
| **Pagination** | Yes (page-based, 25/page) |

**Filter Parameters:**

| Parameter | Type | Match | Example |
|---|---|---|---|
| `id` | UUID | Exact | `?id=<uuid>` |
| `hsn_code` | string | Case-insensitive exact | `?hsn_code=6101` |
| `gst_slab` | number | Exact | `?gst_slab=5` |
| `product_type` | string | Case-insensitive exact | `?product_type=apparel` |
| `brand` | string | Case-insensitive contains | `?brand=levi` |
| `category` | string | Case-insensitive contains | `?category=shirts` |
| `gender_target` | string | Case-insensitive exact | `?gender_target=men` |

**Search Fields** (via `?search=<term>`): `name`, `hsn_code`, `brand`, `description`, `material`

**Ordering Fields** (via `?ordering=<field>`): `created_at`, `name`, `gst_slab`, `product_type`

**Response Shape (200):**

```json
{
  "count": 42,
  "next": "https://<host>/api/v1/products/?page=2",
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "name": "Basmati Rice",
      "hsn_code": "1006",
      "gst_slab": "5.00",
      "product_type": "grocery",
      "brand": "Lal Qila",
      "category": "Rice & Grains",
      "subcategory": "Basmati",
      "description": "Premium aged basmati rice",
      "image_url": "https://...",
      "dietary_type": "veg",
      "material": null,
      "gender_target": "none",
      "fit_type": null,
      "created_at": "2026-06-01T10:00:00Z",
      "variants": [
        {
          "id": "uuid",
          "sku": "DW-RICE-1KG",
          "barcode": "8901234567890",
          "size": "1kg",
          "color": null,
          "stock_quantity": 150,
          "retail_price": "120.00",
          "mrp": "130.00",
          "weight_volume": "1 kg",
          "net_quantity": "1.00",
          "unit_of_measure": "kg",
          "created_at": "2026-06-01T10:00:00Z"
        }
      ]
    }
  ]
}
```

---

##### `POST /api/v1/products/` — Create Product

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**

```json
{
  "name": "Cotton T-Shirt",
  "hsn_code": "6109",
  "gst_slab": "12.00",
  "product_type": "apparel",
  "brand": "Levi's",
  "category": "T-Shirts",
  "material": "100% Cotton",
  "gender_target": "men",
  "fit_type": "Regular Fit"
}
```

**Response (201):** Returns the created product object (same fields as GET detail, without `variants`).

---

##### `GET /api/v1/products/<uuid:id>/` — Product Detail

| Property | Value |
|---|---|
| **Auth** | `AllowAny` (public) |

**Response (200):** Full product object with nested `variants` array.

---

##### `PATCH /api/v1/products/<uuid:id>/` — Update Product

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:** Partial update — send only fields to change.

---

##### `PUT /api/v1/products/<uuid:id>/` — Replace Product

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:** Full replacement — send all writable fields.

---

##### `DELETE /api/v1/products/<uuid:id>/` — Delete Product

| Property | Value |
|---|---|
| **Auth** | `IsManager` (manager only) |

**Response:** `204 No Content`

---

#### 5.2 Product Variants (Spec #06)

##### `GET /api/v1/products/<uuid:product_id>/variants/` — List Variants for a Product

| Property | Value |
|---|---|
| **Auth** | `AllowAny` (public) |
| **Pagination** | Yes |

**Filter Parameters:**

| Parameter | Type | Match | Example |
|---|---|---|---|
| `sku` | string | Case-insensitive contains | `?sku=DW-S` |
| `color` | string | Case-insensitive exact | `?color=blue` |
| `size` | string | Case-insensitive exact | `?size=M` |

**Search Fields** (via `?search=<term>`): `sku`, `barcode`

**Response (200):** Paginated list of `ProductVariant` objects.

---

##### `POST /api/v1/products/<uuid:product_id>/variants/` — Create Variant

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**

```json
{
  "sku": "DW-TSHIRT-M-BLU",
  "barcode": "8901234567891",
  "size": "M",
  "color": "Blue",
  "stock_quantity": 50,
  "retail_price": "599.00",
  "mrp": "799.00",
  "unit_of_measure": "pcs"
}
```

**Response (201):** Created `ProductVariant` object.

---

#### 5.3 Checkout Reservations (Spec #07)

##### `POST /api/v1/checkout/reserve/` — Create a Stock Hold

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` (any logged-in user) |

**Request Body:**

```json
{
  "variant_id": "<uuid>",
  "quantity": 2
}
```

> `quantity` is optional and defaults to `1`.

**Response (201):**

```json
{
  "reservation_id": "<uuid>",
  "variant_id": "<uuid>",
  "reserved_quantity": 2,
  "expires_at": "2026-06-01T10:10:00Z",
  "status": "active"
}
```

**Error Responses:**

| Status | Condition | Response Body |
|---|---|---|
| `400` | Missing/invalid `variant_id` or `quantity` | `{ "error": "..." }` |
| `404` | Variant UUID not found | `{ "error": "SKU variant not found." }` |
| `409` | Insufficient stock (ATP exhausted) | `{ "error": "Insufficient stock available.", "atp": 0, "requested": 2 }` |
| `503` | Lock contention (another transaction in progress) | `{ "error": "Inventory is being updated. Please retry in a moment." }` |

> **Frontend note:** On `409`, display the `atp` value to the user. On `503`, implement an exponential backoff retry.

---

##### `GET /api/v1/checkout/reserve/list/` — List Active Reservations

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Returns only the calling user's active, non-expired reservations with full variant details nested.

**Response (200):**

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "<uuid>",
      "variant": {
        "id": "<uuid>",
        "sku": "DW-RICE-1KG",
        "barcode": "8901234567890",
        "size": "1kg",
        "color": null,
        "stock_quantity": 150,
        "retail_price": "120.00",
        "mrp": "130.00",
        "weight_volume": "1 kg",
        "net_quantity": "1.00",
        "unit_of_measure": "kg",
        "created_at": "2026-06-01T10:00:00Z"
      },
      "reserved_quantity": 2,
      "expires_at": "2026-06-01T10:10:00Z",
      "status": "active"
    }
  ]
}
```

---

##### `DELETE /api/v1/checkout/reserve/<uuid:reservation_id>/` — Release Reservation

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` (owner or staff/manager) |

**Response:** `204 No Content`

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | No active reservation with that ID |
| `403` | User is not the owner and not staff/manager |

---

#### 5.4 Order Confirmation & Management (Spec #08)

##### `POST /api/v1/orders/confirm/` — Confirm Reservation into a Paid Order

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

**Request Body:**

```json
{
  "reservation_id": "<uuid>",
  "payment_method": "UPI"
}
```

> `payment_method` must be one of: `"UPI"`, `"card"`, `"cash"`.

**Response (201):**

```json
{
  "order_id": "<uuid>",
  "total_amount": "133.92",
  "gst_amount": "13.92",
  "payment_method": "UPI",
  "payment_status": "completed",
  "created_at": "2026-06-01T10:05:00Z"
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Missing `reservation_id` or `payment_method`; invalid payment method |
| `404` | Reservation not found, not active, or not owned by caller |
| `409` | Insufficient physical stock after lock acquisition |
| `410 Gone` | Reservation has expired (10-minute window passed) |
| `503` | Database transaction error |

> **Frontend note:** On `410`, prompt the user to restart the checkout flow. The expired reservation's stock is automatically freed.

---

##### `GET /api/v1/orders/` — List Orders

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |
| **Pagination** | Yes |

- **Customers** see only their own orders (filtered by `user_id`).
- **Staff/Managers** see all orders.

**Response (200):** Paginated list of `Order` objects.

---

##### `GET /api/v1/orders/<uuid:id>/` — Order Detail

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` (user-isolated or staff/manager) |

**Response (200):** Single `Order` object.

---

#### 5.5 Barcode Generation (Spec #09)

##### `GET /api/v1/barcodes/<sku>/code128/` — Generate Code 128 Barcode

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |
| **Response Content-Type** | `image/png` |

**Query Parameters:**

| Parameter | Required | Description |
|---|---|---|
| `batch_id` | No | Optional batch identifier to encode alongside the SKU |

**Response:** Raw PNG image stream of the barcode.

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | SKU not found in the database |

---

##### `GET /api/v1/barcodes/<sku>/ean13/` — Generate EAN-13 Barcode

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |
| **Response Content-Type** | `image/png` |

**Response:** Raw PNG image stream of the EAN-13 barcode.

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | SKU not found in the database |
| `422` | SKU is not a valid 12- or 13-digit numeric string for EAN-13 |

> **Frontend note:** Use these endpoints as `<img src="...">` sources. The response includes `Cache-Control: no-store` — barcodes are always freshly generated.

---

#### 5.6 Loose Product Repackaging (Spec #09b)

##### `POST /api/v1/packaging-jobs/` — Create a Packaging Job

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**

```json
{
  "source_description": "50kg Basmati Rice — Lal Qila INV-2026-001",
  "source_variant_id": "<uuid or null>",
  "bulk_quantity_used": "50.000",
  "bulk_unit": "kg",
  "notes": "Packed on machine #2",
  "outputs": [
    {
      "product_id": "<uuid>",
      "sku": "DW-RICE-1KG-PACK",
      "weight_per_packet": "1.000",
      "unit_of_measure": "kg",
      "packets_produced": 48,
      "retail_price": "120.00"
    },
    {
      "product_id": "<uuid>",
      "sku": "DW-RICE-500G-PACK",
      "weight_per_packet": "0.500",
      "unit_of_measure": "kg",
      "packets_produced": 4,
      "retail_price": "65.00"
    }
  ]
}
```

**Key Behaviours:**
- If `sku` doesn't exist under the given `product_id`, a new `ProductVariant` is auto-created (`is_new_variant: true` in the response).
- Stock is automatically incremented by `packets_produced` for each output.
- Barcodes are automatically generated and assigned to new variants.
- The entire operation is wrapped in a database transaction — all-or-nothing.
- `unit_of_measure` choices: `unit`, `kg`, `g`, `litre`, `ml`.
- `bulk_unit` choices: `unit`, `kg`, `g`, `litre`, `ml`.

**Response (201):** Full `PackagingJob` object with nested `outputs` including `barcode_image_url`.

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Validation errors, product not found, SKU belongs to different product |
| `404` | `source_variant_id` not found |
| `503` | Database transaction error |

---

##### `GET /api/v1/packaging-jobs/` — List Packaging Jobs

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |
| **Pagination** | Yes |

**Response (200):** Paginated list of `PackagingJob` objects with nested outputs.

---

##### `GET /api/v1/packaging-jobs/<uuid:id>/` — Packaging Job Detail

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response (200):** Single `PackagingJob` object with full nested outputs.

---

#### 5.7 Invoice Ingestion (Spec #10)

##### `POST /api/v1/invoices/upload/` — Upload an Invoice

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |
| **Content-Type** | `multipart/form-data` |

**Form Data:**

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | file | Yes | Invoice file (PDF, JPEG, PNG, or WebP) |

**Constraints:**

| Constraint | Value |
|---|---|
| Max file size | 20 MB |
| Allowed formats | `application/pdf`, `image/jpeg`, `image/png`, `image/webp` |

**Response (202 Accepted):**

```json
{
  "invoice_id": "<uuid>",
  "status": "processing",
  "task_name": "<cloud-task-id>",
  "message": "Invoice uploaded successfully. OCR extraction is in progress."
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | No file provided (key must be `"file"`) |
| `413` | File exceeds 20 MB limit |
| `415` | Unsupported file type |

> **Frontend note:** After upload, poll the invoice detail endpoint to check when `status` transitions from `processing` to `review` or `confirmed`.

---

##### `GET /api/v1/invoices/` — List Invoices

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |
| **Pagination** | Yes |

**Response (200):** Paginated list of `PurchaseInvoice` objects (without nested `line_items`).

---

##### `GET /api/v1/invoices/<uuid:id>/` — Invoice Detail

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response (200):**

```json
{
  "id": "<uuid>",
  "invoice_number": "INV-2026-001",
  "vendor_name": "Lal Qila Foods",
  "vendor_gstin": "07AABCU9603R1ZX",
  "issued_at": "2026-06-01",
  "gcs_object_path": "invoices/...",
  "status": "review",
  "uploaded_by": "<uuid>",
  "created_at": "2026-06-01T10:00:00Z",
  "signed_url": "https://storage.googleapis.com/...",
  "line_items": [
    {
      "id": "<uuid>",
      "sku": "DW-RICE-1KG",
      "description": "Basmati Rice 1kg Pack",
      "quantity": 100,
      "unit_price": "85.00",
      "gst_rate": "5.00",
      "confidence_score": "0.950",
      "needs_review": false
    }
  ]
}
```

> **Frontend note:** The `signed_url` field provides a 30-minute temporary URL to view/download the original uploaded file. It may be `null` if GCS signed URL generation fails.

---

#### 5.8 HITL Invoice Validation & Confirmation (Spec #12)

##### `GET /api/v1/invoices/<uuid:id>/review/` — Review Invoice Details

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response (200):**
```json
{
  "id": "<uuid>",
  "invoice_number": "INV-2026-001",
  "vendor_name": "Lal Qila Foods",
  "vendor_gstin": "07AABCU9603R1ZX",
  "issued_at": "2026-06-01",
  "gcs_object_path": "invoices/...",
  "status": "review",
  "uploaded_by": "<uuid>",
  "created_at": "2026-06-01T10:00:00Z",
  "signed_image_url": "https://storage.googleapis.com/...",
  "review_summary": {
    "total_items": 1,
    "needs_review_count": 0
  },
  "line_items": [
    {
      "id": "<uuid>",
      "sku": "DW-RICE-1KG",
      "description": "Basmati Rice 1kg Pack",
      "quantity": 100,
      "unit_price": "85.00",
      "gst_rate": "5.00",
      "confidence_score": "0.950",
      "needs_review": false
    }
  ]
}
```

> **Frontend note:** `signed_image_url` provides a 30-minute temporary signed URL for side-by-side rendering in the HITL dashboard.

---

##### `PATCH /api/v1/invoices/line-items/<uuid:id>/` — Correct Line Item

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "sku": "DW-RICE-1KG",
  "quantity": 105,
  "unit_price": "84.50"
}
```

> **Key Behaviour:** Clear `needs_review` flag to `false` automatically when a line item is corrected. Edits are blocked (409) if the invoice is already confirmed.

**Response (200):** Returns the updated `InvoiceLineItem` object.

---

##### `POST /api/v1/invoices/<uuid:id>/confirm/` — Confirm Invoice Stock Ingestion

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response (200):**
```json
{
  "invoice_id": "<uuid>",
  "status": "confirmed",
  "stock_updates_applied": 1,
  "matched_skus": ["DW-RICE-1KG"],
  "unmatched_skus": []
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `409` | Invoice not in `review` or `confirmed` status |
| `422` | Invoice contains items that still have `needs_review = true` |

> **Key Behaviour:** Commits stock increments atomically by SKU. Re-confirming an already confirmed invoice acts as an idempotent success response (200) without re-applying stock. Unmatched SKUs log a warning but do not block confirmation.


---

#### 5.9 ONDC Seller Node (Beckn Protocol) (Spec #13)

Endpoints implementing ONDC Beckn Protocol v1.2.5. All endpoints return HTTP 202 immediately with an ACK and delegate the actual business logic asynchronously to a background task, which dispatches a callback POST to BAP's callback URI (`bap_uri/on_<action>`).

##### `POST /api/v1/ondc/search/` — Discovery Catalog Query
##### `POST /api/v1/ondc/select/` — Item Selection & Reservation Hold
##### `POST /api/v1/ondc/init/` — Billing & Shipping Initialisation (GST CGST/SGST/IGST tax calculation)
##### `POST /api/v1/ondc/confirm/` — Final Order Payment & Confirmation
##### `POST /api/v1/ondc/status/` — Order Status Query
##### `POST /api/v1/ondc/cancel/` — Release Reservation or Cancel Confirmed Order

**Verification / Signature Security:**
Calls must contain an `Authorization` header carrying an Ed25519 signature verified against the ONDC registry key:
`Authorization: Signature keyId="...",algorithm="ed25519",created="...",expires="...",headers="(created) (expires) digest",signature="..."`
If `ONDC_REGISTRY_PUBLIC_KEY_B64` is not configured, signature verification is bypassed for local development/testing.
 
---
 
#### 5.10 WhatsApp Commerce Engine (Spec #14)
 
Endpoints for Meta WhatsApp Business API webhook integration.
 
##### `GET /api/v1/whatsapp/webhook/` — Webhook verification challenge
 
| Property | Value |
|---|---|
| **Auth** | Public |
 
**Query Parameters:**
 
| Parameter | Type | Required | Description |
|---|---|---|---|
| `hub.mode` | string | Yes | Must be `"subscribe"` |
| `hub.verify_token` | string | Yes | Must match `WA_VERIFY_TOKEN` |
| `hub.challenge` | string | Yes | Challenge string from Meta |
 
**Response (200):** Plain text challenge string.
 
---
 
##### `POST /api/v1/whatsapp/webhook/` — Process inbound WhatsApp message
 
| Property | Value |
|---|---|
| **Auth** | Public (verified via `X-Hub-Signature-256`) |
 
**Security / Signature Validation:**
Uses HMAC-SHA256 of the request payload using `WA_APP_SECRET` to verify authenticity. The computed signature must match the `X-Hub-Signature-256` header (with `sha256=` prefix). Signature check is bypassed in local development if `WA_APP_SECRET` is not set.
 
**Request Body (Meta Webhook Structure):**
```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "15555555555",
              "phone_number_id": "123456789"
            },
            "contacts": [{"profile": {"name": "User Name"}, "wa_id": "12345"}],
            "messages": [
              {
                "from": "12345",
                "id": "wamid.HBgLMjMzNzg0NTQ2MTEVAgASGBIwRDQ4NzhDM0E4RjkzRjAyOUQA",
                "timestamp": "1645600000",
                "text": {"body": "check stock SILK-SCARF-RED"},
                "type": "text"
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```
 
**Key Behaviours:**
- **Status Updates/Delivery Receipts**: Payloads without the `messages` key are ignored and return `200` with `status: ignored`.
- **Intent Parsing**: Incoming text is parsed into:
  - `catalog` -> Sends a WhatsApp interactive list picker showing top 10 products.
  - `stock_check` -> Extracts SKU from message, calculates real-time Available-to-Promise (ATP = physical stock - active reservations), and sends stock details along with a CTA web button.
  - `order_status` -> Sends instructions to track orders on the website.
  - `fallback` -> Default help menu.
 
---
 
### 6. Standard Error Response Format

All error responses follow this shape:

```json
{
  "error": "Human-readable error message."
}
```

Some endpoints include additional context fields:

```json
{
  "error": "Insufficient stock available.",
  "atp": 0,
  "requested": 2
}
```

#### Common HTTP Status Codes

| Status | Meaning | Frontend Action |
|---|---|---|
| `200` | Success | Display data |
| `201` | Created | Show success confirmation |
| `202` | Accepted (async processing started) | Start polling for status |
| `204` | No Content (successful deletion) | Remove item from UI |
| `400` | Bad Request (validation error) | Show field-level errors |
| `401` | Unauthorized (auth failure) | Redirect to login |
| `403` | Forbidden (insufficient role) | Show permission denied message |
| `404` | Not Found | Show "not found" state |
| `409` | Conflict (stock exhausted) | Show stock unavailable with ATP |
| `410` | Gone (reservation expired) | Prompt user to restart checkout |
| `413` | Payload Too Large | Show file size error |
| `415` | Unsupported Media Type | Show supported formats |
| `422` | Unprocessable Entity | Show validation error |
| `503` | Service Unavailable (lock contention / DB error) | Retry with exponential backoff |

---

### 7. Implementation Status

#### Completed — Ready for Frontend Integration

| Spec | Feature | Endpoints |
|---|---|---|
| #03 | JWT Authentication | _(middleware — all endpoints)_ |
| #05 | RBAC Permissions | _(middleware — all endpoints)_ |
| #06 | Product Catalog API | `products/`, `products/<id>/`, `products/<id>/variants/` |
| #07 | Checkout Reservations | `checkout/reserve/`, `checkout/reserve/list/`, `checkout/reserve/<id>/` |
| #08 | Order Confirmation | `orders/confirm/`, `orders/`, `orders/<id>/` |
| #09 | Barcode Generation | `barcodes/<sku>/code128/`, `barcodes/<sku>/ean13/` |
| #09b | Loose Product Repackaging | `packaging-jobs/`, `packaging-jobs/<id>/` |
| #10 | Invoice Upload & OCR Dispatch | `invoices/upload/`, `invoices/`, `invoices/<id>/` |
| #11 | Document AI OCR Worker | _(internal task worker — `tasks/process-invoice/`)_ |
| #12 | HITL Invoice Validation & Confirmation | `invoices/<id>/review/`, `invoices/line-items/<id>/`, `invoices/<id>/confirm/` |
| #13 | ONDC Seller Node (Beckn Protocol) | `ondc/search/`, `ondc/select/`, `ondc/init/`, `ondc/confirm/`, `ondc/status/`, `ondc/cancel/` |
| #14 | WhatsApp Commerce Engine | `whatsapp/webhook/` |
| #15 | External Partner API Gateway | `external/inventory/sync/`, `external/shipments/update/`, `admin/api-keys/`, `admin/api-keys/<uuid:pk>/revoke/` |
| #16 | Security Hardening & Rate Limiting | `health/`, `auth/logout/` |
| #17 | Payment Gateway (Razorpay) | Payment processing — will add payment initiation/webhook endpoints |
| #18 | POS Cash Sales & In-Store Billing | Point-of-sale terminal backend |

---

### 8. Quick Reference — Full URL Map

```
BASE: /api/v1/

# ── Product Catalog (Public Read / Staff+ Write) ────────────────────────
GET     /api/v1/products/                                  → List products
POST    /api/v1/products/                                  → Create product
GET     /api/v1/products/<uuid:id>/                        → Product detail
PATCH   /api/v1/products/<uuid:id>/                        → Update product
PUT     /api/v1/products/<uuid:id>/                        → Replace product
DELETE  /api/v1/products/<uuid:id>/                        → Delete product (Manager only)
GET     /api/v1/products/<uuid:product_id>/variants/       → List variants
POST    /api/v1/products/<uuid:product_id>/variants/       → Create variant

# ── Checkout (Authenticated Users) ──────────────────────────────────────
POST    /api/v1/checkout/reserve/                          → Create stock hold
GET     /api/v1/checkout/reserve/list/                     → List active reservations
DELETE  /api/v1/checkout/reserve/<uuid:reservation_id>/    → Release reservation

# ── Orders (Authenticated Users) ────────────────────────────────────────
POST    /api/v1/orders/confirm/                            → Confirm order
GET     /api/v1/orders/                                    → List orders
GET     /api/v1/orders/<uuid:id>/                          → Order detail

# ── Barcodes (Staff/Manager) ────────────────────────────────────────────
GET     /api/v1/barcodes/<sku>/code128/?batch_id=<opt>     → Code 128 barcode PNG
GET     /api/v1/barcodes/<sku>/ean13/                      → EAN-13 barcode PNG

# ── Packaging Jobs (Staff/Manager) ──────────────────────────────────────
POST    /api/v1/packaging-jobs/                            → Create packaging job
GET     /api/v1/packaging-jobs/                            → List packaging jobs
GET     /api/v1/packaging-jobs/<uuid:id>/                  → Packaging job detail

# ── Invoices (Staff/Manager) ────────────────────────────────────────────
POST    /api/v1/invoices/upload/                           → Upload invoice file
GET     /api/v1/invoices/                                  → List invoices
GET     /api/v1/invoices/<uuid:id>/                        → Invoice detail + signed URL

# ── HITL Invoice Validation & Confirmation (Staff/Manager) ──────────────
GET     /api/v1/invoices/<uuid:id>/review/                 → Review invoice details + signed URL
PATCH   /api/v1/invoices/line-items/<uuid:id>/             → Correct line item fields
POST    /api/v1/invoices/<uuid:id>/confirm/                → Confirm invoice stock ingestion

# ── Internal Task Workers (Cloud Tasks Only) ────────────────────────────
POST    /api/v1/tasks/process-invoice/                     → Process invoice background worker

# ── ONDC Seller Node (Public Webhooks / Beckn Signature Verified) ─────────────
POST    /api/v1/ondc/search/                               → Discovery catalog search
POST    /api/v1/ondc/select/                               → Selection stock reservation hold
POST    /api/v1/ondc/init/                                 → Billing details initialisation
POST    /api/v1/ondc/confirm/                              → Payment confirmation & stock decrement
POST    /api/v1/ondc/status/                               → Order status check
POST    /api/v1/ondc/cancel/                               → Release reservation or cancel order
POST    /api/v1/ondc/tasks/callback/                       → Internal Cloud Task background worker
 
# ── WhatsApp Commerce Engine (Public Webhooks / Meta Challenge-Signature Verified) ─
GET     /api/v1/whatsapp/webhook/                          → WhatsApp webhook verification challenge
POST    /api/v1/whatsapp/webhook/                          → Process incoming WhatsApp message

# ── External Partner & Admin API Key Gateway (Completed) ──────────────────
POST    /api/v1/external/inventory/sync/                   → Reconcile ERP inventory stock delta
PATCH   /api/v1/external/shipments/update/                 → Update shipment status from logistics webhook
GET     /api/v1/admin/api-keys/                            → List external partner API keys (Manager only)
POST    /api/v1/admin/api-keys/                            → Create external partner API key (Manager only)
POST    /api/v1/admin/api-keys/<uuid:pk>/revoke/            → Revoke external partner API key (Manager only)

# ── Security Hardening & Rate Limiting (Completed) ────────────────────────
GET     /api/v1/health/                                    → Service liveness health check
POST    /api/v1/auth/logout/                               → Revoke session token (logout)
```