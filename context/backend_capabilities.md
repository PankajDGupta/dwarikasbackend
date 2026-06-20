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
| `active_promotion` | nested object | No | Active promotion details if applicable (read-only, nullable) |
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
| `effective_price` | decimal(12,2) | Price after discount resolved at reservation hold time (nullable) |
| `promotion_id` | UUID | The applied promotion ID (nullable) |
| `coupon_id` | UUID | The applied coupon ID (nullable) |
| `coupon_discount` | decimal(12,2) | Total coupon discount amount applied to the reservation (nullable) |
| `final_price` | decimal(12,2) | Total final price after promotion and coupon discounts (nullable) |


#### 4.4 Order

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `user_id` | UUID | Supabase user (nullable for guest checkout) |
| `total_amount` | decimal(12,2) | Subtotal + GST |
| `gst_amount` | decimal(12,2) | Computed GST amount |
| `payment_method` | enum | `UPI`, `card`, `cash`, `online` *(Razorpay-managed — added Spec #17)* |
| `payment_status` | enum | `pending`, `completed`, `failed`, `refunded` *(added Spec #17)* |
| `carrier_status` | enum | `staged`, `picked_up`, `in_transit`, `delivered` *(added Spec #15)* |
| `tracking_reference` | string | Logistics tracking reference *(added Spec #15)* |
| `created_at` | datetime | ISO 8601 creation timestamp |

#### 4.9 PaymentTransaction *(Spec #17)*

Audit log for every payment attempt tied to a reservation. Separate from `Order` to allow multiple retries per reservation (e.g., user abandons payment and retries).

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `reservation_id` | UUID (FK) | Reservation this payment is for (nullable after SET NULL) |
| `order_id` | UUID (FK) | Order created on successful payment (nullable until paid) |
| `user_id` | UUID | Supabase user who initiated payment |
| `razorpay_order_id` | string | Razorpay Order ID (`order_XXXXXXXX`) — unique, created server-side |
| `razorpay_payment_id` | string | Razorpay Payment ID (`pay_XXXXXXXX`) — filled after payment |
| `razorpay_signature` | string | HMAC-SHA256 signature — filled after server-side verification |
| `amount_paise` | integer | Total charged amount in paise (₹1 = 100 paise) |
| `currency` | string | Always `INR` for domestic payments |
| `status` | enum | `created` → `attempted` → `paid` / `failed` / `refunded` |
| `failure_reason` | string | Human-readable failure description (populated on failure) |
| `created_at` | datetime | ISO 8601 creation timestamp |
| `updated_at` | datetime | ISO 8601 last-updated timestamp |


#### 4.10 Promotion *(Spec #19)*

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `title` | string | Promotion name/title |
| `description` | string | Detailed explanation of the offer (nullable) |
| `discount_type` | enum | `percentage` or `flat_amount` |
| `discount_value` | decimal(10,2) | Discount value (percentage or flat amount in rupees) |
| `max_discount_cap` | decimal(10,2) | Max discount cap (nullable) |
| `min_order_value` | decimal(10,2) | Minimum order value needed (nullable) |
| `banner_image_url` | string | Image link for carousels (nullable) |
| `starts_at` | datetime | ISO 8601 promotion start timestamp |
| `ends_at` | datetime | ISO 8601 promotion end timestamp (nullable) |
| `is_active` | boolean | Flag enabling/disabling the promotion |
| `created_by` | UUID | Manager who created the promotion |
| `created_at` | datetime | ISO 8601 creation timestamp |

#### 4.11 PromotionItem *(Spec #19)*

Defines target scope (either an entire product or a specific variant).

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `promotion_id` | UUID (FK) | Parent promotion |
| `product_id` | UUID (FK) | Target product (nullable) |
| `variant_id` | UUID (FK) | Target variant (nullable) |
| `created_at` | datetime | ISO 8601 creation timestamp |

#### 4.12 PromotionBroadcast *(Spec #19)*

Audit log of WhatsApp broadcast attempts to customers.

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `promotion_id` | UUID (FK) | Broadcasted promotion |
| `phone_number` | string | Target customer phone number |
| `status` | enum | `sent` or `failed` |
| `failure_reason` | string | Description of WhatsApp API failure (nullable) |
| `sent_at` | datetime | ISO 8601 broadcast timestamp |
| `sent_by` | UUID | Manager who triggered the broadcast |


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

#### 4.13 Coupon *(Spec #20)*

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `code` | string | Alphanumeric coupon code (uppercase) |
| `description` | string | Detailed explanation of the coupon (nullable) |
| `discount_type` | enum | `percentage` or `flat_amount` |
| `discount_value` | decimal(10,2) | Discount value (percentage or flat amount in rupees) |
| `max_discount_cap` | decimal(10,2) | Max discount cap (nullable) |
| `min_order_value` | decimal(10,2) | Minimum order value needed (nullable) |
| `max_uses` | integer | Maximum times this coupon can be redeemed globally (nullable) |
| `uses_per_user` | integer | Max uses per individual user (default: 1) |
| `specific_user_id` | UUID | Specific user user_id if restricted to one customer (nullable) |
| `is_active` | boolean | Flag enabling/disabling the coupon |
| `valid_from` | datetime | ISO 8601 coupon start timestamp |
| `valid_until` | datetime | ISO 8601 coupon end timestamp (nullable) |
| `created_by` | UUID | Manager who created the coupon |
| `created_at` | datetime | ISO 8601 creation timestamp |
| `source` | enum | `manual`, `gaming_reward`, `referral` |

#### 4.14 CouponRedemption *(Spec #20)*

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `coupon_id` | UUID (FK) | Redeemed coupon |
| `user_id` | UUID | User who redeemed the coupon |
| `order_id` | UUID (FK) | Order associated with the redemption (nullable) |
| `reservation_id` | UUID (FK) | Reservation associated with the redemption (nullable) |
| `discount_applied` | decimal(12,2) | Final calculated discount amount |
| `redeemed_at` | datetime | ISO 8601 redemption timestamp |

#### 4.15 DiscountSuggestion *(Spec #22)*

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `variant` | nested object | Full ProductVariant object (on reads) |
| `discount_score` | integer | Composite score (0-100) |
| `priority` | enum | `critical`, `high`, `medium` |
| `reason_summary` | string | Human-readable explanation of score |
| `reasons` | object | Score breakdown details |
| `suggested_discount_type` | enum | `percentage`, `flat_amount` |
| `suggested_discount_value` | decimal(10,2) | Auto-suggested discount value |
| `suggested_ends_days` | integer | Suggested promotion duration in days |
| `current_stock` | integer | Stock quantity snapshot during analysis |
| `avg_monthly_sales` | decimal(10,2) | Average monthly sales trailing 90 days |
| `days_since_last_order` | integer | Days since last completed order for variant |
| `cost_price` | decimal(12,2) | Unit cost price from latest confirmed invoice |
| `margin_pct` | decimal(5,2) | Calculated gross margin percentage |
| `status` | enum | `pending`, `approved`, `dismissed`, `expired` |
| `dismissed_until` | datetime | Snooze deadline after dismissal |
| `approved_promotion` | UUID (FK) | Reference to created Promotion if approved |
| `analysed_at` | datetime | When this suggestion was last computed |
| `created_at` | datetime | ISO 8601 creation timestamp |

#### 4.16 AmazonCredentials *(Spec #23)*

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `seller_id` | string | Amazon Seller Central ID (unique) |
| `lwa_client_id` | string | Login with Amazon client ID |
| `lwa_client_secret` | string | LWA client secret |
| `lwa_refresh_token` | string | Ephemeral auth refresh token |
| `region` | enum | SP-API regional endpoint key (`NA`, `EU`, `FE`) |
| `primary_marketplace_id` | string | Primary marketplace ID (e.g. `A21TJRUUN4KGV` for India) |
| `authorized_at` | datetime | ISO 8601 authorization timestamp |
| `updated_at` | datetime | ISO 8601 update timestamp |

#### 4.17 AmazonListing *(Spec #23)*

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `product_id` | UUID (FK) | Core product relation reference |
| `sku` | string | Matches core product SKU (unique) |
| `asin` | string | Assigned Amazon Standard Identification Number (nullable) |
| `marketplace_id` | string | Target marketplace ID |
| `sync_status` | enum | `PENDING`, `SUBMITTED`, `ACTIVE`, `INVALID`, `ERROR`, `SUPPRESSED` |
| `submission_id` | UUID | putListingsItem SP-API submission GUID (nullable) |
| `validation_issues` | array | List of validation issue descriptions (JSONB) |
| `price_synced` | decimal(10,2)| Latest price uploaded to Amazon (nullable) |
| `quantity_synced` | integer | Latest quantity uploaded to Amazon |
| `last_synced_at` | datetime | ISO 8601 synchronisation completion timestamp |
| `created_at` | datetime | ISO 8601 creation timestamp |
| `updated_at` | datetime | ISO 8601 update timestamp |

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
 
#### 5.11 Amazon SP-API One-Click Product Listing (Spec #23)
 
Endpoints enabling store administrators to list catalog products on Amazon Marketplaces with a single click. Uses the synchronous Listings Items API (v2021-08-01) for real-time validation feedback, with downstream asynchronous status tracking via Amazon SNS/SQS webhooks.
 
##### `POST /api/v1/amazon/listings/sync/` — Trigger Amazon Listing Submission
 
| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |
 
**Request Body:**
```json
{
  "product_id": "b1ca2914-75dd-11ea-bc55-0242ac130003",
  "marketplace_id": "A21TJRUUN4KGV"
}
```
 
**Response (202 Accepted):**
```json
{
  "success": true,
  "sku": "DWRK-BASMATI-5K",
  "status": "SUBMITTED",
  "submission_id": "7df4e528-9844-46ab-8991-6cf1b54c86e2",
  "message": "Listing submission successfully accepted by Amazon. Final state changes will resolve asynchronously."
}
```
 
**Error Responses:**
 
| Status | Condition |
|---|---|
| `400` | Missing `product_id` or `marketplace_id`; product has no EAN/UPC barcode |
| `404` | Product UUID not found in catalog |
| `422` | Amazon SP-API returned `INVALID` status — issues returned in body |
| `429` | SP-API rate limit exceeded; retry after backoff |
| `502` | Amazon SP-API unreachable or returned 5xx |
 
> **Frontend note:** After receiving `202`, poll the status endpoint to track progression from `SUBMITTED` → `ACTIVE` (or `SUPPRESSED`/`INVALID`). On `422`, display the returned `issues` array showing which product attributes need correction.
 
---
 
##### `GET /api/v1/amazon/listings/{product_id}/status/` — Listing Status Check
 
| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |
 
**Response (200 OK):**
```json
{
  "product_id": "b1ca2914-75dd-11ea-bc55-0242ac130003",
  "sku": "DWRK-BASMATI-5K",
  "asin": "B01NXYZ123",
  "sync_status": "ACTIVE",
  "last_synced_at": "2026-06-07T06:45:00Z",
  "issues": []
}
```
 
**sync_status enum values:**
 
| Value | Meaning |
|---|---|
| `PENDING` | Not yet submitted to Amazon |
| `SUBMITTED` | Submission accepted; awaiting catalog processing |
| `ACTIVE` | Live and buyable on Amazon marketplace |
| `INVALID` | Rejected due to structural validation errors |
| `SUPPRESSED` | Active but hidden from search (policy issue) |
| `ERROR` | Unexpected error; check `issues` array |
 
---
 
##### `POST /api/v1/amazon/webhooks/sqs-receiver/` — SQS Status Event Receiver
 
| Property | Value |
|---|---|
| **Auth** | Internal (Amazon SNS signature verified) |
 
Receives `LISTINGS_ITEM_STATUS_CHANGE` and `LISTINGS_ITEM_ISSUES_CHANGE` events from Amazon SNS via SQS. Parses the notification, maps statuses (`BUYABLE` → `ACTIVE`, `SUPPRESSED` → `SUPPRESSED`), and updates the `amazon_listings` table. Populates the `asin` column when provided. Returns `200 OK` immediately to prevent SNS retry loops.
 
---

#### 5.11b Quick-Commerce Channel Integration — Blinkit & JioMart (Spec #24)

This module enables store administrators to list products on Blinkit and JioMart with a single click.

##### `POST /api/v1/quickcommerce/listings/sync/` — Trigger One-Click Listing

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "product_id": "b1ca2914-75dd-11ea-bc55-0242ac130003",
  "platforms": ["jiomart", "blinkit"],
  "fssai_license": "10012345000001",
  "marketplace_config": {
    "jiomart": {
      "location_ids": ["jiomartLocationId1"]
    },
    "blinkit": {
      "vendor_id": "BLK-VND-001",
      "pincodes": ["560067"],
      "has_catalog_match": true
    }
  }
}
```

**Response (202 Accepted):**
```json
{
  "success": true,
  "product_id": "b1ca2914-75dd-11ea-bc55-0242ac130003",
  "results": {
    "jiomart": {
      "status": "SUBMITTED",
      "trace_id": "7f4a2d88-0c23-4b11-9e12-abc123456789",
      "message": "Batch accepted by Fynd Konnect. Polling for COMPLETED status."
    },
    "blinkit": {
      "status": "ACTIVE",
      "matched_upc": "8901234567890",
      "message": "Product matched to existing Blinkit catalog. Inventory sync enabled."
    }
  },
  "validation_warnings": []
}
```

---

##### `GET /api/v1/quickcommerce/listings/{product_id}/status/` — Listing Status Check

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response (200 OK):**
```json
{
  "product_id": "b1ca2914-75dd-11ea-bc55-0242ac130003",
  "listings": [
    {
      "platform": "jiomart",
      "platform_sku": "DW-RICE-1KG",
      "sync_status": "ACTIVE",
      "trace_id": "7f4a2d88-0c23-4b11-9e12-abc123456789",
      "last_synced_at": "2026-06-07T10:45:00Z",
      "validation_issues": []
    },
    {
      "platform": "blinkit",
      "platform_sku": "DW-RICE-1KG",
      "platform_upc": "8901234567890",
      "sync_status": "PENDING_REVIEW",
      "submission_guid": "a1b2c3d4-...",
      "last_synced_at": "2026-06-07T10:45:00Z",
      "validation_issues": []
    }
  ]
}
```

---

##### `POST /api/v1/quickcommerce/blinkit/webhook/po/` — Blinkit Purchase Order Webhook Receiver

| Property | Value |
|---|---|
| **Auth** | Internal (Verified via Blinkit Webhook signature header `X-Blinkit-Signature`) |

**Request Body:**
```json
{
  "event": "PURCHASE_ORDER_CREATED",
  "po_id": "BLK-PO-20260607-001",
  "vendor_id": "BLK-VND-001",
  "items": [
    {
      "sku": "DW-RICE-1KG",
      "upc": "8901234567890",
      "ordered_quantity": 120,
      "unit_price": 115.00,
      "mrp": 135.00
    }
  ],
  "delivery_pincode": "560067",
  "expected_delivery_date": "2026-06-09"
}
```

**Response (200 OK):**
```json
{
  "status": "VERIFIED",
  "po_id": "BLK-PO-20260607-001"
}
```

---

##### `POST /api/v1/quickcommerce/blinkit/asn/submit/` — Advanced Shipping Note (ASN) Submission

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "po_id": "BLK-PO-20260607-001",
  "dispatched_items": [
    {
      "sku": "DW-RICE-1KG",
      "dispatched_quantity": 120,
      "batch_number": "BATCH-2026-0607"
    }
  ],
  "tracking_reference": "DTDC-12345678"
}
```

**Response (200 OK):**
```json
{
  "asn_reference": "ASN-DWR-20260608-001",
  "po_id": "BLK-PO-20260607-001",
  "status": "ASN_SENT",
  "message": "Advanced Shipping Note transmitted to Blinkit dark store successfully."
}
```

---

##### `POST /api/v1/quickcommerce/jiomart/webhook/order/` — JioMart Webhook Order Ingestion

| Property | Value |
|---|---|
| **Auth** | Internal (verified via JioMart UAT OAuth configuration) |

**Request Body:**
```json
{
  "order_id": "8c59f0f9-2e06-4b95-a228-36c1e95cfc1d",
  "location_id": "jiomartLocationId1",
  "total_amount": 1200.00
}
```

**Response (200 OK):**
```json
{
  "order_id": "8c59f0f9-2e06-4b95-a228-36c1e95cfc1d",
  "facility_code": "FacilityA",
  "status": "staged",
  "shipping_label_url": "https://cdn.jiomart.com/labels/mock-label.pdf"
}
```

---

##### `POST /api/v1/quickcommerce/jiomart/manifest/close/` — JioMart Manifest Closure

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "jiomart_order_id": "8c59f0f9-2e06-4b95-a228-36c1e95cfc1d",
  "manifest_id": "MFT-20260608-001"
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "order_id": "8c59f0f9-2e06-4b95-a228-36c1e95cfc1d",
  "carrier_status": "picked_up",
  "tracking_reference": "MFT-20260608-001"
}
```

---

##### `GET /api/v1/quickcommerce/metrics/` — Operational Performance Metrics

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

**Query Parameters:**
- `platform` (string: `blinkit` | `jiomart`)
- `from_date` (date string)
- `to_date` (date string)

**Response (200 OK):**
```json
{
  "platform": "blinkit",
  "period": {
    "from": "2026-06-01",
    "to": "2026-06-07"
  },
  "otif_rate": 96.2,
  "fill_rate": 98.7,
  "inventory_discrepancy_margin": 1.3,
  "total_pos": 48,
  "on_time_pos": 46,
  "in_full_pos": 47
}
```

---

#### 5.12 Payment Gateway — Razorpay *(Spec #17)*

The payment flow is a **three-step server-driven sequence**. All amounts are computed server-side from the database. The frontend never sends an amount.

##### Flow Summary

```
Customer Browser              Django Backend             Razorpay
     |                             |                          |
     |-- POST /payments/create-order/ -->                     |
     |   { reservation_id }        |                          |
     |                    create_razorpay_order() ----------->|
     |<--------------------------- |<------- { order_id } ---|
     |<-- { razorpay_order_id,     |                          |
     |     razorpay_key_id,        |                          |
     |     amount_paise }          |                          |
     |                             |                          |
     |  [User completes payment in Razorpay widget]           |
     |                             |                          |
     |<-- { payment_id, signature }|-- payment.captured -->   |
     |                             |                          |
     |-- POST /payments/verify/ -->|                          |
     |   { order_id, payment_id,   |                          |
     |     signature }             |                          |
     |                   verify_payment_signature()           |
     |                   [atomic: stock--, order++]           |
     |<-- { order_id, status: 'completed' }                   |
     |                             |                          |
     |                   POST /payments/webhook/ <------------|
     |                   (idempotent no-op if paid)           |
```

> **Security invariant:** `RAZORPAY_KEY_SECRET` never leaves the server. Signature verification uses `hmac.compare_digest` to prevent timing attacks. All amounts are computed from the database — the frontend sends zero financial values.

---

##### `POST /api/v1/payments/create-order/` — Step 1: Create Razorpay Order

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` (any logged-in user) |

**Request Body:**

```json
{
  "reservation_id": "<uuid>"
}
```

**Response (201):**

```json
{
  "razorpay_order_id": "order_XXXXXXXXXXXXXXXX",
  "razorpay_key_id": "rzp_test_xxxxxxxxxxxx",
  "amount_paise": 120000,
  "currency": "INR",
  "reservation_id": "<uuid>"
}
```

> **Frontend note:** Pass `razorpay_order_id`, `razorpay_key_id`, and `amount_paise` to the Razorpay JS Checkout widget to render the payment UI. The `razorpay_key_id` is the public key — safe to use in the browser.

**Key Behaviours:**
- Amount is computed server-side as `subtotal + GST` in paise. Frontend never sends an amount.
- **Idempotent:** calling this twice for the same reservation returns the existing Razorpay order ID (no duplicate charge).
- Returns `410 Gone` if the reservation has expired.

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Missing or invalid `reservation_id` |
| `404` | No active reservation found for this user |
| `410` | Reservation has expired — prompt user to restart checkout |
| `502` | Razorpay API unreachable |

---

##### `POST /api/v1/payments/verify/` — Step 2: Verify & Commit

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Called by the frontend **after** the Razorpay JS SDK returns success. Verifies the HMAC-SHA256 signature, then atomically: decrements stock, marks reservation `completed`, creates the `Order`, marks the `PaymentTransaction` as `paid`.

**Request Body:**

```json
{
  "razorpay_order_id": "order_XXXXXXXXXXXXXXXX",
  "razorpay_payment_id": "pay_XXXXXXXXXXXXXXXX",
  "razorpay_signature": "<hmac-sha256-hex>"
}
```

**Response (201):**

```json
{
  "order_id": "<uuid>",
  "total_amount": "1200.00",
  "gst_amount": "183.05",
  "payment_status": "completed",
  "razorpay_payment_id": "pay_XXXXXXXXXXXXXXXX"
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Missing parameters; or HMAC signature verification failed (no stock decrement) |
| `404` | Payment transaction not found |
| `409` | Insufficient physical stock at commit time |
| `410` | Reservation expired before payment could be committed |
| `503` | Database error |

> **Frontend note:** This is idempotent — calling verify twice with the same `razorpay_order_id` returns the existing order without re-decrementing stock.

---

##### `POST /api/v1/payments/webhook/` — Razorpay Async Notification

| Property | Value |
|---|---|
| **Auth** | None (verified via `X-Razorpay-Signature` HMAC header using `RAZORPAY_WEBHOOK_SECRET`) |
| **CSRF** | Exempt (`@csrf_exempt`) |

Razorpay's authoritative payment confirmation path. Required for UPI collect, net banking, and auto-debit flows where the user redirect may never reach the frontend. This endpoint is the ground truth — not the frontend `verify/` call.

**Handled Events:**

| Event | Action |
|---|---|
| `payment.captured` | Idempotently commits stock + creates order (no-op if `verify/` already ran) |
| `payment.failed` | Marks `PaymentTransaction.status = 'failed'` |
| `refund.created` | Marks `PaymentTransaction.status = 'refunded'` |

Always returns `HTTP 200` to Razorpay — failure to do so causes webhook retries.

> **Frontend note:** The frontend does not interact with this endpoint. It is called by Razorpay's servers directly.

---

##### `POST /api/v1/payments/refund/` — Issue Refund

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

Staff/Manager-only. Calls the Razorpay Refunds API and atomically reverses the stock decrement.

**Request Body:**

```json
{
  "order_id": "<uuid>",
  "reason": "Customer requested cancellation"
}
```

**Response (200):**

```json
{
  "razorpay_refund_id": "rfnd_XXXXXXXXXXXXXXXX",
  "amount_refunded_paise": 120000,
  "status": "refunded"
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `403` | Caller is not staff or manager |
| `404` | No paid transaction found for this order |
| `502` | Razorpay Refund API call failed |
| `207` | Refund issued but stock reversal failed — manual inventory correction required |

---

##### `GET /api/v1/payments/status/<uuid:order_id>/` — Payment Status

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` (owner sees own; staff/manager sees any) |

**Response (200):**

```json
{
  "order_id": "<uuid>",
  "razorpay_order_id": "order_XXXXXXXXXXXXXXXX",
  "razorpay_payment_id": "pay_XXXXXXXXXXXXXXXX",
  "status": "paid",
  "amount_paise": 120000,
  "currency": "INR",
  "failure_reason": null,
  "created_at": "2026-06-08T10:00:00Z"
}
```

**status enum values:**

| Value | Meaning |
|---|---|
| `created` | Razorpay order created, user hasn't paid yet |
| `attempted` | User started payment flow |
| `paid` | Payment confirmed and order committed |
| `failed` | Payment failed or signature mismatch |
| `refunded` | Full refund issued |


---

#### 5.13 POS Cash Sales & In-Store Bill Generation *(Spec #18)*

Physical store checkout flow where floor staff scans multiple items for a walk-in customer paying cash.

##### `POST /api/v1/pos/cart/` — Create POS Cart

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body (optional):**
```json
{
  "customer_phone": "9876543210"
}
```

**Response (201):**
```json
{
  "cart_id": "uuid",
  "status": "open",
  "customer_phone": "9876543210"
}
```

---

##### `POST /api/v1/pos/cart/<uuid:cart_id>/items/` — Add Item to Cart

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "variant_id": "uuid",
  "quantity": 2
}
```

**Response (201 or 200 if upserted):**
```json
{
  "item_id": "uuid",
  "variant_id": "uuid",
  "sku": "DW-RICE-1KG",
  "product_name": "Basmati Rice",
  "quantity": 2,
  "unit_price": "120.00",
  "line_subtotal": "240.00"
}
```

---

##### `DELETE /api/v1/pos/cart/<uuid:cart_id>/items/<uuid:item_id>/` — Remove Item from Cart

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response:** `204 No Content`

---

##### `GET /api/v1/pos/cart/<uuid:cart_id>/` — View Cart Detail

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response (200):**
```json
{
  "cart_id": "uuid",
  "status": "open",
  "customer_phone": "9876543210",
  "items": [
    {
      "item_id": "uuid",
      "variant_id": "uuid",
      "sku": "DW-RICE-1KG",
      "product_name": "Basmati Rice",
      "hsn_code": "1006",
      "gst_slab": "18.00",
      "quantity": 2,
      "unit_price": "120.00",
      "subtotal": "240.00",
      "gst_amount": "43.20",
      "line_total": "283.20",
      "atp_available": 48,
      "stock_ok": true
    }
  ],
  "totals": {
    "subtotal": "240.00",
    "total_gst": "43.20",
    "grand_total": "283.20"
  }
}
```

---

##### `POST /api/v1/pos/cart/<uuid:cart_id>/confirm/` — Confirm POS Cash Sale

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "cash_tendered": "500.00",
  "payment_method": "cash"
}
```

**Response (201):**
```json
{
  "bill_type": "POS_CASH",
  "order_id": "uuid",
  "order_date": "2026-06-08T18:00:00Z",
  "customer_phone": "9876543210",
  "line_items": [
    {
      "sku": "DW-RICE-1KG",
      "product_name": "Basmati Rice",
      "hsn_code": "1006",
      "gst_slab": "18.00",
      "quantity": 2,
      "unit_price": "120.00",
      "subtotal": "240.00",
      "cgst": "21.60",
      "sgst": "21.60",
      "gst_total": "43.20",
      "line_total": "283.20"
    }
  ],
  "totals": {
    "subtotal": "240.00",
    "total_cgst": "21.60",
    "total_sgst": "21.60",
    "total_gst": "43.20",
    "grand_total": "283.20"
  },
  "payment": {
    "method": "cash",
    "cash_tendered": "500.00",
    "change_due": "216.80"
  },
  "store": {
    "name": "Dwarikas",
    "address": "123 Retail Lane",
    "gstin": "YOUR_GSTIN_HERE"
  }
}
```

---

##### `DELETE /api/v1/pos/cart/<uuid:cart_id>/abandon/` — Abandon POS Cart

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response:** `204 No Content`

---

##### `GET /api/v1/pos/bill/<uuid:order_id>/` — Reprint/Re-fetch Printable Bill

| Property | Value |
|---|---|
| **Auth** | `IsStaffOrManager` |

**Response (200):** Same structure as the `confirm` bill payload response.

---

#### 5.14 Promotions & Discounts (Spec #19)

Manage, link, and broadcast time-bound promotions.

##### `GET /api/v1/promotions/` — List Promotions

| Property | Value |
|---|---|
| **Auth** | `AllowAny` (public storefront) or `IsManager` (for managing) |
| **Pagination** | Yes |

**Key Behaviour:**
- Public requests (anonymous or `customer` role) return only currently live and active promotions (`starts_at <= now <= ends_at` and `is_active = true`).
- Manager requests bypass validity checks to return all configured promotions.

##### `POST /api/v1/promotions/` — Create Promotion

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

**Request Body:**
```json
{
  "title": "Diwali Special Offer",
  "description": "20% off on premium apparel items",
  "discount_type": "percentage",
  "discount_value": "20.00",
  "max_discount_cap": "500.00",
  "min_order_value": "1000.00",
  "starts_at": "2026-06-08T00:00:00Z",
  "ends_at": "2026-06-15T23:59:59Z",
  "is_active": true
}
```

##### `GET /api/v1/promotions/<uuid:id>/` — Get Promotion Detail

| Property | Value |
|---|---|
| **Auth** | `AllowAny` (public storefront) |

##### `PATCH /api/v1/promotions/<uuid:id>/` — Update Promotion

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `DELETE /api/v1/promotions/<uuid:id>/` — Delete Promotion

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `GET /api/v1/promotions/active/` — Carousel Active List

| Property | Value |
|---|---|
| **Auth** | `AllowAny` |

Returns only currently live promotions sorted by their upcoming expiration times (`ends_at`), optimized for high-performance storefront banner carousels.

##### `POST /api/v1/promotions/<uuid:promotion_id>/items/` — Link Product/Variant

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

Link a promotion to a product or specific variant.

**Request Body:**
```json
{
  "product": "uuid_of_product",
  "variant": "uuid_of_variant_or_null"
}
```

##### `DELETE /api/v1/promotions/<uuid:promotion_id>/items/<uuid:item_id>/` — Unlink Product/Variant

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `POST /api/v1/promotions/<uuid:id>/share/whatsapp/` — Broadcast Promotion

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

Triggers an asynchronous WhatsApp CTA message broadcast to a customer phone number list.

**Request Body:**
```json
{
  "phone_numbers": ["919876543210", "918765432109"],
  "message_override": "Custom broadcast greeting message",
  "store_url": "https://dwarikas.com/shop"
}
```

**Response (200):**
```json
{
  "promotion_id": "<uuid>",
  "total_sent": 2,
  "total_failed": 0
}
```

---

#### 5.16 Coupon Code Creation & Application (Spec #20)

##### `GET /api/v1/coupons/` — List Coupons

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `POST /api/v1/coupons/` — Create Coupon

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

**Request Body:**
```json
{
  "code": "WELCOME100",
  "description": "Welcome discount of flat Rs 100",
  "discount_type": "flat_amount",
  "discount_value": "100.00",
  "max_discount_cap": null,
  "min_order_value": "500.00",
  "max_uses": 100,
  "uses_per_user": 1,
  "specific_user_id": null,
  "is_active": true,
  "valid_from": "2026-06-12T12:00:00Z",
  "valid_until": "2026-06-30T23:59:59Z",
  "source": "manual"
}
```

##### `GET /api/v1/coupons/<uuid:id>/` — Coupon Detail

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `PATCH /api/v1/coupons/<uuid:id>/` — Update Coupon

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `DELETE /api/v1/coupons/<uuid:id>/` — Delete Coupon

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `GET /api/v1/coupons/validate/<str:code>/?reservation_id=<uuid>` — Validate Coupon

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Checks if a coupon code is valid for the calling user, calculating the prospective discount and final price against a given `reservation_id` (optional). Does not reserve or apply the coupon.

**Response (200):**
```json
{
  "valid": true,
  "code": "WELCOME100",
  "discount_type": "flat_amount",
  "discount_value": "100.00",
  "discount_amount": "100.00",
  "final_total": "400.00"
}
```

##### `POST /api/v1/checkout/apply-coupon/` — Apply Coupon to Reservation

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Validates the coupon code and atomically applies it to the active reservation, creating a pending `CouponRedemption` (with `order_id` as `NULL`) to reserve the usage limit.

**Request Body:**
```json
{
  "reservation_id": "uuid_of_reservation",
  "coupon_code": "WELCOME100"
}
```

**Response (200):**
```json
{
  "reservation_id": "uuid_of_reservation",
  "coupon_code": "WELCOME100",
  "discount_amount": "100.00",
  "final_total": "400.00",
  "message": "Coupon applied successfully."
}
```

##### `DELETE /api/v1/checkout/remove-coupon/<uuid:reservation_id>/` — Remove Coupon from Reservation

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Removes the applied coupon from the active reservation and deletes the pending `CouponRedemption` record, releasing the usage count.

**Response (200):**
```json
{
  "message": "Coupon removed successfully."
}
```

---

#### 5.17 Gaming Engine Integration & Coupon Rewards (Spec #21)

##### `GET /api/v1/gaming/earn/` — Get Play Quota

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Returns play quota statistics for the user, including order-derived plays and ad-watch-derived plays.

**Response (200):**
```json
{
  "user_id": "00000000-0000-0000-0000-000000000001",
  "order_plays_earned": 12,
  "order_plays_used": 10,
  "order_plays_remaining": 2,
  "ad_plays_granted_today": 2,
  "ad_plays_remaining_today": 3,
  "ad_plays_limit_per_day": 5,
  "ad_plays_available_to_play": 1,
  "total_plays_remaining": 3,
  "plays_calculation": "1 play per confirmed order + up to 5 ad plays per day"
}
```

##### `POST /api/v1/gaming/record-play/` — Record Game Play

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Consumes a play slot and records a game session. On win, generates a coupon reward and optionally sends WhatsApp message.

**Request Body:**
```json
{
  "game_session_id": "session-unique-guid",
  "game_type": "spin_wheel",
  "won": true,
  "win_level": "jackpot"
}
```

**Response (200 - Win):**
```json
{
  "play_id": "uuid-of-play",
  "plays_remaining": 2,
  "won": true,
  "coupon_code": "GAME-XXXX",
  "coupon_discount_type": "percentage",
  "coupon_discount_value": "25.00",
  "coupon_valid_until": "2026-07-20"
}
```

**Response (200 - Loss):**
```json
{
  "play_id": "uuid-of-play",
  "plays_remaining": 2,
  "won": false,
  "coupon_code": null
}
```

##### `GET /api/v1/gaming/ad-status/` — Get Rewarded Ad Quota Status

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Returns whether the user can watch a rewarded ad to earn a free play.

**Response (200):**
```json
{
  "can_watch_ad": true,
  "ad_plays_granted_today": 2,
  "ad_plays_remaining_today": 3,
  "ad_plays_limit_per_day": 5,
  "resets_at": "2026-06-14T00:00:00+00:00"
}
```

##### `POST /api/v1/gaming/grant-ad-play/` — Grant Ad-powered Free Play

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Called after watching a rewarded ad to grant +1 play. Enforces the daily cap of 5.

**Request Body:**
```json
{
  "ad_placement_id": "Rewarded_Android",
  "ad_unit_id": "abc123"
}
```

**Response (200 - Granted):**
```json
{
  "granted": true,
  "ad_plays_granted_today": 3,
  "ad_plays_remaining_today": 2,
  "total_plays_remaining": 4
}
```

**Response (403 - Daily Limit Reached):**
```json
{
  "granted": false,
  "detail": "Daily ad play limit reached. Come back tomorrow!",
  "resets_at": "2026-06-14T00:00:00+00:00"
}
```

##### `GET /api/v1/gaming/rewards/` — List User Rewards

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` |

Lists all gaming rewards won by the calling user.

**Response (200):**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "reward-uuid",
      "game_type": "spin_wheel",
      "win_level": "jackpot",
      "reward_tier_name": "Grand Prize",
      "coupon": {
        "id": "coupon-uuid",
        "code": "GAME-ABC123",
        "description": "Grand Prize reward from gaming",
        "discount_type": "percentage",
        "discount_value": "25.00",
        "max_discount_cap": "50.00",
        "min_order_value": null,
        "max_uses": 1,
        "uses_per_user": 1,
        "specific_user_id": "user-uuid",
        "is_active": true,
        "valid_from": "2026-06-13T14:40:00Z",
        "valid_until": "2026-07-13T14:40:00Z",
        "created_by": "service-account-uuid",
        "created_at": "2026-06-13T14:40:00Z",
        "source": "gaming_reward"
      },
      "whatsapp_sent": true,
      "whatsapp_delivered": null,
      "created_at": "2026-06-13T14:40:00Z"
    }
  ]
}
```

##### `GET /api/v1/gaming/rewards/<uuid:id>/` — Reward Detail

| Property | Value |
|---|---|
| **Auth** | `IsAuthenticated` (owner-isolated) |

Returns details of a single reward won by the user.

##### `GET /api/v1/gaming/reward-tiers/` — List Reward Tiers

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

Lists all configured reward tiers.

##### `POST /api/v1/gaming/reward-tiers/` — Create Reward Tier

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

##### `PATCH /api/v1/gaming/reward-tiers/<uuid:id>/` — Update Reward Tier

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

---

#### 5.13 Smart Discount Suggestions Engine (Spec #22)

##### `GET /api/v1/promotions/suggestions/` — List Suggestions

| Property | Value |
|---|---|
| **Auth** | `IsManager` |
| **Pagination** | Yes |

**Filter Parameters:**
- `priority` (string: `critical` | `high` | `medium`)

**Response (200):**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "suggestion-uuid",
      "variant": {
        "id": "variant-uuid",
        "sku": "DW-RICE-1KG",
        "retail_price": "100.00",
        "stock_quantity": 50,
        "product": {
          "name": "Test Rice",
          "category": "Grocery"
        }
      },
      "discount_score": 85,
      "priority": "critical",
      "reason_summary": "High stock & low sales",
      "reasons": {
        "stock_score": 80,
        "recency_score": 90
      },
      "suggested_discount_type": "percentage",
      "suggested_discount_value": "20.00",
      "suggested_ends_days": 7,
      "current_stock": 50,
      "avg_monthly_sales": "2.50",
      "days_since_last_order": 45,
      "cost_price": "50.00",
      "margin_pct": "50.00",
      "status": "pending",
      "dismissed_until": null,
      "approved_promotion": null,
      "analysed_at": "2026-06-14T13:30:00Z",
      "created_at": "2026-06-14T13:30:00Z"
    }
  ]
}
```

##### `GET /api/v1/promotions/suggestions/<uuid:id>/` — Suggestion Detail

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

**Response (200):** Specific suggestion object.

##### `POST /api/v1/promotions/suggestions/<uuid:id>/approve/` — Approve Suggestion

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

**Request Body (Optional overrides):**
```json
{
  "title": "Clearance Sale — 20% Off",
  "ends_days": 7,
  "discount_value": 20.00
}
```

**Response (201):**
```json
{
  "suggestion_id": "suggestion-uuid",
  "promotion_id": "promotion-uuid",
  "title": "Clearance Sale — 20% Off",
  "discount_type": "percentage",
  "discount_value": "20.00",
  "starts_at": "2026-06-14T13:30:00Z",
  "ends_at": "2026-06-21T13:30:00Z",
  "message": "Promotion is now live."
}
```

##### `POST /api/v1/promotions/suggestions/<uuid:id>/dismiss/` — Dismiss Suggestion

| Property | Value |
|---|---|
| **Auth** | `IsManager` |

**Request Body (Optional snooze duration):**
```json
{
  "snooze_days": 30
}
```

**Response (200):**
```json
{
  "message": "Suggestion dismissed for 30 days."
}
```

##### `POST /api/v1/tasks/run-discount-analysis/` — Run Background Job

| Property | Value |
|---|---|
| **Auth** | Internal check (Cloud Tasks OIDC / debug mode) |

**Response (200):**
```json
{
  "status": "ok",
  "new_suggestions": 1,
  "updated_suggestions": 0,
  "expired_suggestions": 0
}
```

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
| #17 | Payment Gateway (Razorpay) | `payments/create-order/`, `payments/verify/`, `payments/webhook/`, `payments/refund/`, `payments/status/<id>/` |
| #18 | POS Cash Sales & In-Store Billing | `pos/cart/`, `pos/cart/<id>/`, `pos/cart/<id>/items/`, `pos/cart/<id>/confirm/`, `pos/bill/<order_id>/` |
| #19 | Promotions & Discounts | `promotions/`, `promotions/<id>/`, `promotions/active/`, `promotions/<id>/items/`, `promotions/<id>/share/whatsapp/` |
| #20 | Coupon Code Creation & Application | `coupons/`, `coupons/<id>/`, `coupons/validate/<code>/`, `checkout/apply-coupon/`, `checkout/remove-coupon/<reservation_id>/` |
| #21 | Gaming Engine Integration & Coupon Rewards | `gaming/earn/`, `gaming/record-play/`, `gaming/ad-status/`, `gaming/grant-ad-play/`, `gaming/rewards/`, `gaming/rewards/<id>/`, `gaming/reward-tiers/`, `gaming/reward-tiers/<id>/` |
| #22 | Smart Discount Suggestions Engine | `promotions/suggestions/`, `promotions/suggestions/<id>/`, `promotions/suggestions/<id>/approve/`, `promotions/suggestions/<id>/dismiss/` |
| #23 | Amazon SP-API One-Click Listing | `amazon/listings/sync/`, `amazon/listings/<id>/status/`, `amazon/webhooks/sqs-receiver/` |
| #24 | Blinkit & JioMart One-Click Listing | `quickcommerce/listings/sync/`, `quickcommerce/listings/<id>/status/`, `quickcommerce/blinkit/webhook/po/`, `quickcommerce/blinkit/asn/submit/`, `quickcommerce/jiomart/webhook/order/`, `quickcommerce/jiomart/manifest/close/`, `quickcommerce/metrics/` |


 
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

# ── POS Cash Sales & In-Store Billing (Completed) ──────────────────────────
POST    /api/v1/pos/cart/                                  → Create POS cart session
POST    /api/v1/pos/cart/<uuid:cart_id>/items/             → Add item/quantity to cart
DELETE  /api/v1/pos/cart/<uuid:cart_id>/items/<uuid:item_id>/ → Remove item from cart
GET     /api/v1/pos/cart/<uuid:cart_id>/                   → View cart contents with ATP
POST    /api/v1/pos/cart/<uuid:cart_id>/confirm/           → Confirm POS sale atomically
DELETE  /api/v1/pos/cart/<uuid:cart_id>/abandon/           → Abandon POS cart session
GET     /api/v1/pos/bill/<uuid:order_id>/                  → Reprint/Re-fetch invoice

# ── Payment Gateway / Razorpay (Completed) ──────────────────────────
POST    /api/v1/payments/create-order/               → Step 1: Create Razorpay order (returns razorpay_order_id + amount)
POST    /api/v1/payments/verify/                     → Step 2: Verify HMAC signature + atomic stock commit
POST    /api/v1/payments/webhook/                    → Razorpay async webhook (payment.captured / failed / refund.created)
POST    /api/v1/payments/refund/                     → Issue refund + reverse stock (Staff/Manager only)
GET     /api/v1/payments/status/<uuid:order_id>/     → Payment status for an order

# ── Promotions & Discounts (Completed) ──────────────────────────────────
GET     /api/v1/promotions/                                → List promotions (storefront/public or manager list)
POST    /api/v1/promotions/                                → Create promotion (Manager only)
GET     /api/v1/promotions/<uuid:id>/                      → Get promotion details (public)
PATCH   /api/v1/promotions/<uuid:id>/                      → Update promotion details (Manager only)
DELETE  /api/v1/promotions/<uuid:id>/                      → Delete promotion (Manager only)
GET     /api/v1/promotions/active/                         → Get active promotions list for storefront banner
POST    /api/v1/promotions/<uuid:promotion_id>/items/      → Link product/variant to promotion (Manager only)
DELETE  /api/v1/promotions/<uuid:promotion_id>/items/<uuid:item_id>/ → Unlink product/variant (Manager only)
POST    /api/v1/promotions/<uuid:id>/share/whatsapp/       → Share promotion via WhatsApp broadcast (Manager only)

# ── Coupon Code Creation & Application (Completed) ─────────────────────────
GET     /api/v1/coupons/                                   → List coupons (Manager only)
POST    /api/v1/coupons/                                   → Create coupon (Manager only)
GET     /api/v1/coupons/<uuid:id>/                         → Coupon detail (Manager only)
PATCH   /api/v1/coupons/<uuid:id>/                         → Update coupon (Manager only)
DELETE  /api/v1/coupons/<uuid:id>/                         → Delete coupon (Manager only)
GET     /api/v1/coupons/validate/<str:code>/               → Validate coupon for caller (preview discount)
POST    /api/v1/checkout/apply-coupon/                     → Validate and apply coupon to active reservation
DELETE  /api/v1/checkout/remove-coupon/<uuid:res_id>/      → Remove applied coupon from active reservation

# ── External Partner & Admin API Key Gateway (Completed) ──────────────────
POST    /api/v1/external/inventory/sync/                   → Reconcile ERP inventory stock delta
PATCH   /api/v1/external/shipments/update/                 → Update shipment status from logistics webhook
GET     /api/v1/admin/api-keys/                            → List external partner API keys (Manager only)
POST    /api/v1/admin/api-keys/                            → Create external partner API key (Manager only)
POST    /api/v1/admin/api-keys/<uuid:pk>/revoke/           → Revoke external partner API key (Manager only)

# ── Security Hardening & Rate Limiting (Completed) ──────────────────────────
GET     /api/v1/health/                                    → Service liveness health check
POST    /api/v1/auth/logout/                               → Revoke session token (logout)

# ── Gaming Engine Integration & Coupon Rewards (Spec #21 — Completed) ────────────
GET     /api/v1/gaming/earn/                               → Get play quota (order & ad based)
POST    /api/v1/gaming/record-play/                        → Consume play and generate win coupon reward
GET     /api/v1/gaming/ad-status/                          → Get rewarded ad watch quota status
POST    /api/v1/gaming/grant-ad-play/                      → Grant play after ad watch completion
GET     /api/v1/gaming/rewards/                            → List user's earned rewards
GET     /api/v1/gaming/rewards/<uuid:id>/                  → Detail of a user's earned reward
GET     /api/v1/gaming/reward-tiers/                       → List reward tier configurations (Manager only)
POST    /api/v1/gaming/reward-tiers/                       → Create reward tier configuration (Manager only)
GET     /api/v1/gaming/reward-tiers/<uuid:id>/             → Retrieve reward tier configuration (Manager only)
PATCH   /api/v1/gaming/reward-tiers/<uuid:id>/             → Update reward tier configuration (Manager only)

# ── Smart Discount Suggestions Engine (Completed) ────────────────────────
GET     /api/v1/promotions/suggestions/                    → List pending discount suggestions (Manager only)
GET     /api/v1/promotions/suggestions/<uuid:id>/          → Retrieve specific discount suggestion (Manager only)
POST    /api/v1/promotions/suggestions/<uuid:id>/approve/  → Approve suggestion to create a live promotion (Manager only)
POST    /api/v1/promotions/suggestions/<uuid:id>/dismiss/  → Dismiss suggestion (snooze) (Manager only)
POST    /api/v1/tasks/run-discount-analysis/               → Trigger background analytics job (Cloud Tasks only)

# ── Amazon SP-API Marketplace Listing (Spec #23 — Completed) ──────────────────
POST    /api/v1/amazon/listings/sync/                      → Trigger one-click Amazon listing submission
GET     /api/v1/amazon/listings/<uuid:product_id>/status/  → Listing sync status poll
POST    /api/v1/amazon/webhooks/sqs-receiver/              → Amazon SNS/SQS status event receiver

# ── Quick-Commerce Listing Integration (Spec #24 — Completed) ─────────────────
POST    /api/v1/quickcommerce/listings/sync/               → Trigger one-click Blinkit/JioMart listings
GET     /api/v1/quickcommerce/listings/<uuid:product_id>/status/ → Listings status check
POST    /api/v1/quickcommerce/blinkit/webhook/po/          → Blinkit B2B PO webhook receiver
POST    /api/v1/quickcommerce/blinkit/asn/submit/          → Advanced Shipping Note (ASN) submission
POST    /api/v1/quickcommerce/jiomart/webhook/order/       → JioMart order webhook receiver
POST    /api/v1/quickcommerce/jiomart/manifest/close/      → JioMart manifest closure
GET     /api/v1/quickcommerce/metrics/                     → Quick-commerce operational metrics dashboard (Manager only)
```