# Spec #24 — Unified One-Click Product Listing: Blinkit & JioMart Integration

**Project:** Dwarika's Omnichannel Tech Ecosystem  
**Target Capability:** One-Click Product Listing on Blinkit and JioMart Quick-Commerce Platforms  
**Primary Integrations:**
- JioMart via Reliance Fynd 3P Aggregator Gateway (Fynd Konnect API v3)
- Blinkit via EDI/Webhook B2B Vendor Model + Catalog Template Pipeline  
**Django App:** `quickcommerce/`

---

## 1. Executive Summary & Objective

This module enables Dwarika's store administrators to list any catalog product on both **Blinkit** and **JioMart** hyperlocal quick-commerce channels with a single click from the administrative dashboard. The system acts as a **stateful middleware abstraction layer** that:

1. Ingests master product data from Dwarika's central Supabase catalog.
2. Normalizes and validates product attributes against each platform's listing compliance rules.
3. Routes catalog payloads through two fundamentally different ingestion pipelines.
4. Manages downstream order fulfillment lifecycles (PO processing, ASN generation, returns).
5. Tracks key operational performance metrics (OTIF, Fill Rate, Inventory Discrepancy Margin).

Because JioMart and Blinkit operate on entirely different technology stacks and vendor models, the middleware runs **two distinct ingestion pipelines** behind a single unified API surface.

### Key Architectural Metrics

| Metric | Target |
|---|---|
| **One-Click Listing Trigger** | Admin submits listing; pre-flight validation completes in < 500 ms |
| **JioMart Batch Acceptance** | Fynd Konnect returns `trace_id` within 2 seconds of batch POST |
| **Blinkit Catalog Matching** | EAN/UPC barcode lookup and SKU-to-catalog link completes in < 1 second |
| **OTIF Rate Target** | ≥ 95% on-time in-full delivery to dark stores |
| **Fill Rate Target** | ≥ 98% quantity fulfillment across all POs |
| **Inventory Discrepancy Margin** | ≤ 2% variance between physical WMS stock and channel-synced stock |

---

## 2. Platform Overview & Technology Stack Comparison

| Dimension | JioMart (via Fynd JCP) | Blinkit (Eternal Network) |
|---|---|---|
| **Architecture** | Cloud-native, Kubernetes-based JioCommerce Platform (JCP) | Invite-only managed vendor model |
| **API Type** | REST + GraphQL; Kafka-backed async event streaming | EDI-based; no public self-serve catalog endpoints |
| **Throughput** | > 10 billion requests/month; < 50 ms response latency | Managed via Category Manager approval + webhook routing |
| **Catalog Ingestion** | Fynd Konnect v3 batch REST API (up to 100 products/request) | Manual CSV/Excel template + Category Manager intake; OR EAN/UPC barcode matching |
| **Authentication** | OAuth 2.0 + API-Key Exchange (header: `x-access-token`) | Managed Vendor ID whitelisting; IP restriction; webhook signatures |
| **Order Model** | Marketplace-shipped; JioMart provides labels and tracking | B2B PO model; self-shipped by seller to dark stores |
| **Inventory Routing** | JSON facility map (`jiomartLocationId` → `UCFacilityCode`) | Pincode-based routing map (`560067` → `BLR_03`) |

---

## 3. High-Level Architecture Diagram

```
+-------------------------------------------------------------------------------------------+
|                            DWARIKA'S ECOSYSTEM BOUNDARY                                   |
|                                                                                           |
|   +------------------+      +--------------------------+      +------------------------+  |
|   | Next.js Admin UI |<====>| Django Backend           |<====>| Supabase PostgreSQL    |  |
|   | (One-Click List) |      | (quickcommerce/ app)     |      | (QC listings, WH maps) |  |
|   +------------------+      +--------------------------+      +------------------------+  |
|                                          |                                                |
|                         +----------------+----------------+                               |
|                         |                                 |                               |
|              +----------v----------+          +-----------v---------+                     |
|              | JioMart Pipeline    |          | Blinkit Pipeline    |                     |
|              | (Async/Batch)       |          | (Semantic Match +   |                     |
|              +----------+----------+          | Template)           |                     |
|                         |                    +-----------+---------+                      |
+-------------------------|------------------              |                                 |
                          |                               |                                 
          +---------------v---------+        +------------v-----------+                    
          | Fynd Konnect Gateway    |        | Blinkit EDI / Webhooks |                    
          | POST /v3/catalog/product|        | (PO Ingestion,  ASN    |                    
          | (Fynd Developer Portal) |        |  Webhook URL)           |                    
          +-------------------------+        +------------------------+                    
                          |                                |                               
          +---------------v---------+        +------------v-----------+                    
          | JioCommerce Platform    |        | Blinkit Dark Store     |                    
          | (JCP / Kafka / Kube)    |        | Network (Eternal)      |                    
          +-------------------------+        +------------------------+                    
```

---

## 4. Unified Schema Normalization & Data Mapping

The middleware validates and transforms Dwarika's master product attributes into platform-specific fields before transmission.

### 4.1 Unified Mapping Table

| Master Parameter | JioMart / Fynd Field | Blinkit Field | Validation Rule |
|---|---|---|---|
| `master_sku` | `item_code` | `sku` / `item_code` | String; unique internal-to-platform mapping |
| `barcode_value` | `identifiers.value` (Primary EAN) | `product_upc` | Exactly 13 digits; must pass EAN-13 / GTIN check-digit validation |
| `product_name` | `name` | `sku_name` | String 1–300 chars; must lead with brand name |
| `selling_price` | `selling_price` | `selling_price` | Decimal(10,2); must not exceed `mrp` |
| `mrp` | `mrp` | `mrp` | Decimal(10,2); exact match between PO and physical label required |
| `tax_rule_id` | `tax_identifier.tax_rule_id` | `hsn_code` / `tax_slab` | Validated against HSN register |
| `category` | `category` (L3 Category Name) | `marketplace_category` | Must match active platform taxonomy |
| `brand_name` | `brand` | `brand` | Registered brand string |
| `fssai_license` | `fssai_license` | `fssai_license` | 14-digit active registration; mandatory for food & beverage |
| `primary_image` | `media.url` | `image_url` | Min 1000×1000 px; white background covering ≥ 85% of frame |

### 4.2 Pre-Flight Validation Rules

Before transmitting any listing to either platform, the middleware's validation engine must enforce:

1. **FSSAI License Check:** If `category` is food or beverage, verify a 14-digit active FSSAI license is present in the payload.
2. **Barcode Validation:** Validate that all product records contain a valid 13-digit EAN-13 barcode (check-digit algorithm).
3. **Image Check:** Ensure at least one high-resolution (≥ 1000×1000 px), white-background image is attached.
4. **MRP Parity Check (Blinkit):** The `mrp` in the listing payload must exactly match the MRP printed on the physical product label. If mismatched, the system blocks the fulfillment flow.
5. **Price Sanity Check:** `selling_price` ≤ `mrp` across both platforms.
6. **MOQ Check (JioMart):** Initial stock shipments must include ≥ 500 units per SKU.
7. **Shelf Life Check (JioMart):** Food and perishables must have ≥ 60% of total shelf life remaining at the time of dark store delivery.

---

## 5. Platform-Specific Catalog Ingestion Pipelines

### 5.1 JioMart Pipeline — Programmatic Asynchronous Batch Ingestion (Fynd Konnect)

#### 5.1.1 One-Time Onboarding Sequence (Manual — done once per seller)

The following steps are executed once during seller onboarding and are prerequisites to programmatic listing:

1. **Portal Registration:** Register on the JioMart Seller Portal, then register on the Fynd Platform.
2. **Onboarding Tracking:** Update the integration tracking sheet for team visibility.
3. **Brand & Location Setup:** Register the brand and physical warehouse locations in Fynd dashboard.
4. **Whitelisting & Cataloging:** Verify Fynd whitelisting status; complete catalog approval; install the JioMart Fynd extension.
5. **Integration Channel Setup:** Map selling locations to warehouse store codes; request whitelisting on the JioMart integration gateway; **select the integration channel (permanent — cannot be changed)**.
6. **Credential Generation:** Fynd generates new store codes with `3PP` prefix; extract `Username` and `Token` from the extension settings for WMS/ERP credential configuration.

#### 5.1.2 Programmatic Batch Catalog Creation (Runtime)

**Endpoint:**
```
POST https://fyndkonnect.konnect.uat.fyndx1.de/v3/catalog/product
```

**Required Header:**
```
x-access-token: <FYND_ACCESS_TOKEN>
Content-Type: application/json
```

**Payload Structure (Parent-Child JSON):**
```json
{
  "items": [
    {
      "item_code": "DW-RICE-1KG",
      "name": "Dwarika's Basmati Rice - 1kg",
      "brand": "Dwarika's Farms",
      "category": "Rice & Grains",
      "mrp": 135.00,
      "selling_price": 120.00,
      "tax_identifier": {
        "tax_rule_id": "1006-5pct"
      },
      "identifiers": [
        {
          "type": "EAN",
          "value": "8901234567890"
        }
      ],
      "fssai_license": "10012345000001",
      "media": [
        {
          "url": "https://cdn.dwarikas.com/images/rice-1kg.jpg"
        }
      ],
      "sizes": [
        {
          "size": "1kg",
          "price": 120.00,
          "quantity": 500
        }
      ]
    }
  ]
}
```

> **Batch limit:** Up to 100 products per request.

**Synchronous Response (202 Accepted):**
```json
{
  "trace_id": "7f4a2d88-0c23-4b11-9e12-abc123456789",
  "status": "PROCESSING"
}
```

**Polling Loop (Non-Blocking):**

The middleware enters a non-blocking polling loop using Cloud Tasks:

```
[Batch POST] → [Receive trace_id] → [Store trace_id in DB (status: SUBMITTED)]
     ↓
[Cloud Task: Poll /v3/catalog/batch-status?trace_id=<id> every 15s]
     ↓
[On COMPLETED: parse field-level errors → update qc_platform_listings]
[On ERROR: store validation_issues JSONB → surface to Admin UI]
```

**Polling Endpoint:**
```
GET https://fyndkonnect.konnect.uat.fyndx1.de/v3/catalog/batch-status?trace_id=<id>
```

---

### 5.2 Blinkit Pipeline — Semantic Matching & Template Automation

Blinkit's invite-only vendor model does not expose open catalog creation endpoints. The pipeline uses two branches:

#### 5.2.1 Branch A — Catalog Match (EAN/UPC Already Exists on Blinkit)

```
[One-Click Trigger]
      ↓
[Lookup product_upc (EAN-13) in Blinkit global catalog via API check / vendor portal]
      ↓
[Match Found] → Link seller internal SKU directly to Blinkit UPC in qc_platform_listings
      ↓
[Enable immediate inventory sync via Vendor ID]
```

No new catalog entry is required. The system registers the SKU→UPC link and begins inventory synchronization via the Blinkit webhook endpoint.

#### 5.2.2 Branch B — Template Compilation (New SKU on Blinkit)

```
[No Catalog Match Found]
      ↓
[Compile Blinkit Listing CSV/Excel template]
  - Populate: Product UPC (SKU field), Listing Reference Number (GUID), brand, name,
    category, MRP, selling_price, fssai_license, image_url, hsn_code
      ↓
[Attach high-resolution packaging images]
      ↓
[Route compiled sheet + images → Blinkit Category Manager via configured email/portal]
      ↓
[Status: PENDING_REVIEW in qc_platform_listings]
      ↓
[Category Manager approves → triggers webhook notification → status: ACTIVE]
```

---

## 6. Authentication & Session Cryptography

### 6.1 JioMart / Fynd Authentication

| Layer | Detail |
|---|---|
| **Framework** | OAuth 2.0 + API-Key Exchange |
| **Token Protocol** | Header injection: `x-access-token: <FYND_ACCESS_TOKEN>` |
| **Credential Source** | `Username` and `Token` extracted from the Unicommerce/aggregator Fynd extension settings |
| **Renewal** | Programmatic OAuth 2.0 refresh cycles — rotate access tokens before expiry |
| **Verification** | Signed SSL handshakes with OAuth scope checks |

### 6.2 Blinkit Authentication

| Layer | Detail |
|---|---|
| **Framework** | Managed Vendor ID Whitelisting — keyless design |
| **Credential** | Registered `Vendor ID` / `Receiver Code` (provided by Blinkit Category Manager) |
| **Security Model** | IP whitelisting + webhook signature validation |
| **Webhook Activation** | Must be manually enabled by Blinkit engineering team |
| **Daily Sync** | No API keys or dynamic headers required for inventory/PO data transfer |

---

## 7. Database Schema Design (Supabase PostgreSQL)

### 7.1 Quick-Commerce Platform Registry

```sql
CREATE TYPE qc_platform_enum AS ENUM ('blinkit', 'jiomart');

CREATE TYPE qc_listing_status_enum AS ENUM (
    'DRAFT',
    'VALIDATING',
    'SUBMITTED',
    'PENDING_REVIEW',
    'ACTIVE',
    'INACTIVE',
    'REJECTED',
    'ERROR'
);

CREATE TABLE public.qc_platform_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    variant_id UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,

    -- Platform identity
    platform qc_platform_enum NOT NULL,
    platform_sku TEXT,                        -- Platform-side SKU / item_code
    platform_upc TEXT,                        -- Blinkit UPC or JioMart EAN identifier
    asin_equivalent TEXT,                     -- Platform's internal catalog ID (if any)

    -- Listing state
    sync_status qc_listing_status_enum NOT NULL DEFAULT 'DRAFT',
    trace_id TEXT,                            -- JioMart: Fynd Konnect batch trace_id
    submission_guid UUID,                     -- Blinkit: Listing Reference Number (GUID)
    validation_issues JSONB DEFAULT '[]'::jsonb,

    -- Pricing snapshot at time of listing
    mrp_snapshot NUMERIC(10, 2),
    selling_price_snapshot NUMERIC(10, 2),

    -- Compliance
    fssai_license TEXT,
    barcode_validated BOOLEAN DEFAULT FALSE,
    image_validated BOOLEAN DEFAULT FALSE,

    -- Timestamps
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_qc_listings_product ON public.qc_platform_listings(product_id);
CREATE INDEX idx_qc_listings_platform_sku ON public.qc_platform_listings(platform, platform_sku);
CREATE INDEX idx_qc_listings_status ON public.qc_platform_listings(sync_status);

ALTER TABLE public.qc_platform_listings ENABLE ROW LEVEL SECURITY;

CREATE POLICY qc_staff_admin_access ON public.qc_platform_listings
    FOR ALL USING (auth.jwt() ->> 'role' IN ('staff', 'manager'));
```

### 7.2 Platform Credentials Store

```sql
CREATE TABLE public.qc_platform_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform qc_platform_enum NOT NULL UNIQUE,

    -- JioMart / Fynd
    fynd_username TEXT,
    fynd_access_token TEXT,
    fynd_token_expires_at TIMESTAMPTZ,

    -- Blinkit
    blinkit_vendor_id TEXT,
    blinkit_receiver_code TEXT,
    blinkit_webhook_secret TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.qc_platform_credentials ENABLE ROW LEVEL SECURITY;

CREATE POLICY qc_manager_only ON public.qc_platform_credentials
    FOR ALL USING (auth.jwt() ->> 'role' = 'manager');
```

### 7.3 Warehouse & Location Mapping

```sql
CREATE TABLE public.qc_warehouse_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform qc_platform_enum NOT NULL,
    internal_facility_code TEXT NOT NULL,   -- e.g. "UCFacilityCode1" or "BLR_03"
    platform_location_id TEXT NOT NULL,     -- e.g. "jiomartLocationId1" or pincode "560067"
    mapping_type TEXT NOT NULL CHECK (mapping_type IN ('facility', 'pincode')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_qc_wh_platform_location
    ON public.qc_warehouse_mappings(platform, platform_location_id);
```

### 7.4 Platform Purchase Orders (Blinkit B2B POs)

```sql
CREATE TYPE qc_po_status_enum AS ENUM (
    'RECEIVED', 'VERIFIED', 'DISPATCHED', 'ASN_SENT', 'INWARDED', 'CANCELLED'
);

CREATE TABLE public.qc_purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform qc_platform_enum NOT NULL,
    platform_po_id TEXT NOT NULL UNIQUE,          -- Blinkit's PO reference number
    vendor_id TEXT,                               -- Blinkit Vendor ID
    facility_code TEXT,                           -- Fulfillment warehouse code
    po_status qc_po_status_enum NOT NULL DEFAULT 'RECEIVED',
    total_amount NUMERIC(12, 2),
    asn_reference TEXT,                           -- Advanced Shipping Note ID (post-dispatch)
    raw_payload JSONB,                            -- Full original PO webhook payload
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at TIMESTAMPTZ,
    inwarded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.qc_po_line_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id UUID NOT NULL REFERENCES public.qc_purchase_orders(id) ON DELETE CASCADE,
    variant_id UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    platform_sku TEXT NOT NULL,
    ordered_quantity INTEGER NOT NULL CHECK (ordered_quantity > 0),
    delivered_quantity INTEGER DEFAULT 0 CHECK (delivered_quantity >= 0),
    unit_price NUMERIC(10, 2),
    mrp NUMERIC(10, 2)
);
```

---

## 8. Hyperlocal Warehouse & Location Mapping Configuration

### 8.1 JioMart Multi-Location Inventory Setup

```json
{
  "jiomartLocationId1": "UCFacilityCode1",
  "jiomartLocationId2": "UCFacilityCode2"
}
```

- Each warehouse code (`UCFacilityCode1`) maps to a JioMart location ID.
- When JioMart routes an order, the middleware assigns it to the correct local facility.
- Fynd generates store codes with `3PP` prefix for each active warehouse; these must be registered in the mapping registry.

### 8.2 Blinkit Pincode-Based Routing Map

```json
{
  "560067": "BLR_03",
  "500076": "HYD_02",
  "560068": "HYD_02"
}
```

- Customer delivery pincodes map directly to the merchant's physical facility codes.
- Each registered Blinkit `Vendor ID` acts as an independent fulfillment entity linked to a specific dispatch warehouse.

---

## 9. Downstream Fulfillment & Order Lifecycle

### 9.1 JioMart Order & Return Lifecycle

```
[JioMart Order Placed]
      ↓
[Fetch Order & A4 Invoice + Shipping Label from JioMart API]
      ↓
[Warehouse Pack + Generate Shipping Manifest]
      ↓
[Push Manifest Closure → JioMart → Status: "Dispatched"]
      ↓
[Customer Receives Order]
      ↓
[Returns: CIR / RTO synced automatically via middleware]
      ↓
[Quality check in WMS → update return status → notify JioMart → refund triggered]
```

**JioMart Cancellation Rules:**
- **Seller cancellations:** Allowed only before invoice generation. No cancellations after invoicing.
- **Customer cancellations:** Allowed until "Dispatched" (manifest closure). No post-dispatch cancellations.

**Returns Sync:**
- Customer-initiated returns (CIR) and return-to-origin (RTO) are synced automatically via the middleware's return status listener.

### 9.2 Blinkit B2B PO Fulfillment Flow

```
[Blinkit Automated PO Webhook Received]
      ↓
[Middleware: Verify quantities & MRP against active WMS stock]
      ↓
[MRP Mismatch? → BLOCK fulfillment until reconciled]
      ↓
[MRP Match? → Seller manages shipping to assigned dark store]
      ↓
[Manifest Closed → Generate ASN (Advanced Shipping Note) → Transmit to Blinkit]
      ↓
[Dark Store scans and inwards stock → po_status: INWARDED]
```

**Blinkit Fulfillment Rules:**
- Transactions use Cash on Delivery (COD) under B2B terms.
- Invoice codes and tax details generated locally by the seller's WMS.
- Order splitting is allowed and managed by the middleware.

---

## 10. Operational Performance Metrics

The middleware tracks three core KPIs using automated data pipelines:

### 10.1 On-Time In-Full (OTIF) Rate

$$\text{OTIF} = \left(\frac{P_{\text{on-time}} \cap P_{\text{in-full}}}{P_{\text{total}}}\right) \times 100$$

Where:
- $P_{\text{on-time}}$ = shipments delivered within the scheduled dark store delivery window
- $P_{\text{in-full}}$ = POs fulfilled with zero quantity or SKU discrepancies
- $P_{\text{total}}$ = total POs issued by the platform

**Target:** ≥ 95%

### 10.2 Fill Rate (FR)

$$\text{FR} = \left(\frac{\sum_{i=1}^{n} Q_{\text{delivered},i}}{\sum_{i=1}^{n} Q_{\text{ordered},i}}\right) \times 100$$

Where:
- $Q_{\text{delivered},i}$ = quantity of SKU _i_ received and inwarded at the platform's fulfillment center
- $Q_{\text{ordered},i}$ = target quantity specified in the automated PO

**Target:** ≥ 98%

### 10.3 Inventory Discrepancy Margin (IDM)

$$\text{IDM} = \left(\frac{\sum_{i=1}^{n} |I_{\text{channel},i} - I_{\text{physical},i}|}{\sum_{i=1}^{n} I_{\text{physical},i}}\right) \times 100$$

Where:
- $I_{\text{channel},i}$ = inventory count synced to the platform channel for SKU _i_
- $I_{\text{physical},i}$ = actual physical inventory in the merchant's WMS

**Target:** ≤ 2%

---

## 11. API Specification

### 11.1 Trigger One-Click Listing

| Property | Value |
|---|---|
| **URL** | `POST /api/v1/quickcommerce/listings/sync/` |
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "product_id": "b1ca2914-75dd-11ea-bc55-0242ac130003",
  "platforms": ["jiomart", "blinkit"],
  "marketplace_config": {
    "jiomart": {
      "location_ids": ["jiomartLocationId1"]
    },
    "blinkit": {
      "vendor_id": "BLK-VND-001",
      "pincodes": ["560067", "500076"]
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

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Missing `product_id` or `platforms`; product has no valid EAN-13 barcode; invalid FSSAI license |
| `404` | Product UUID not found in Dwarika's catalog |
| `422` | Pre-flight validation failed (details in `validation_issues`) |
| `409` | MRP mismatch between listing payload and physical product label (Blinkit) |
| `502` | Fynd Konnect or Blinkit endpoint unreachable |

---

### 11.2 Listing Status Check

| Property | Value |
|---|---|
| **URL** | `GET /api/v1/quickcommerce/listings/{product_id}/status/` |
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

**`sync_status` enum values:**

| Value | Meaning |
|---|---|
| `DRAFT` | Not yet submitted |
| `VALIDATING` | Pre-flight checks in progress |
| `SUBMITTED` | Submitted to platform; awaiting processing |
| `PENDING_REVIEW` | Blinkit template routed to Category Manager |
| `ACTIVE` | Live on the platform |
| `INACTIVE` | Temporarily deactivated |
| `REJECTED` | Rejected by platform compliance |
| `ERROR` | Unexpected error; check `validation_issues` |

---

### 11.3 Blinkit PO Webhook Receiver

| Property | Value |
|---|---|
| **URL** | `POST /api/v1/quickcommerce/blinkit/webhook/po/` |
| **Auth** | Internal — verified via Blinkit webhook signature header |

**Request Body (Blinkit PO Webhook):**
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

**Behaviour:**
1. Verify webhook signature using configured `blinkit_webhook_secret`.
2. Resolve `delivery_pincode` → `facility_code` via the warehouse mapping table.
3. Validate MRP against Dwarika's catalog `mrp` field for each SKU — **block and alert if mismatch**.
4. Create a `qc_purchase_orders` record (status: `RECEIVED`) with all line items.
5. Return `200 OK` to prevent Blinkit retry loops.

---

### 11.4 ASN Transmission (After Blinkit Dispatch)

| Property | Value |
|---|---|
| **URL** | `POST /api/v1/quickcommerce/blinkit/asn/submit/` |
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
  "dispatch_date": "2026-06-08T10:00:00Z",
  "tracking_reference": "DTDC-12345678"
}
```

**Response (200):**
```json
{
  "asn_reference": "ASN-DWR-20260608-001",
  "po_id": "BLK-PO-20260607-001",
  "status": "ASN_SENT",
  "message": "Advanced Shipping Note transmitted to Blinkit dark store successfully."
}
```

---

### 11.5 JioMart Order Webhook Receiver

| Property | Value |
|---|---|
| **URL** | `POST /api/v1/quickcommerce/jiomart/webhook/order/` |
| **Auth** | Internal — verified via JioMart OAuth scope + signed SSL |

**Behaviour:**
1. Receive new order event from JioMart.
2. Fetch order details including A4 invoice and shipping label from JioMart API.
3. Map order to local facility via `qc_warehouse_mappings`.
4. Create internal fulfillment record.
5. Return `200 OK`.

---

### 11.6 JioMart Manifest Closure

| Property | Value |
|---|---|
| **URL** | `POST /api/v1/quickcommerce/jiomart/manifest/close/` |
| **Auth** | `IsStaffOrManager` |

**Request Body:**
```json
{
  "jiomart_order_id": "JM-ORD-20260607-0042",
  "manifest_id": "MFT-20260608-001"
}
```

**Response (200):** Confirms manifest closed and JioMart notified; order status transitions to `Dispatched`.

---

### 11.7 Operational Metrics Dashboard

| Property | Value |
|---|---|
| **URL** | `GET /api/v1/quickcommerce/metrics/` |
| **Auth** | `IsManager` |

**Query Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `platform` | string | Filter by `blinkit` or `jiomart` (optional) |
| `from_date` | date | Start of reporting window |
| `to_date` | date | End of reporting window |

**Response (200):**
```json
{
  "platform": "blinkit",
  "period": { "from": "2026-06-01", "to": "2026-06-07" },
  "otif_rate": 96.2,
  "fill_rate": 98.7,
  "inventory_discrepancy_margin": 1.3,
  "total_pos": 48,
  "on_time_pos": 46,
  "in_full_pos": 47
}
```

---

## 12. Step-by-Step Integration Setup Guide

### Step 1: Fynd Platform Integration & Token Setup

1. Navigate to **Extensions** in the Fynd Platform panel → search for the Unicommerce/aggregator connector → click **Install**.
2. Open extension settings → copy the generated **Username** and **Token** — store in `qc_platform_credentials` table (via Django Secret Manager injection).
3. In the extension's selling locations tab, select warehouse store codes and toggle to **Active**. Note the newly generated `3PP`-prefixed store codes.
4. Configure the warehouse mapping in `qc_warehouse_mappings`:
   ```json
   { "jiomartLocationId1": "UCFacilityCode1" }
   ```

### Step 2: Blinkit Webhook Routing Configuration

1. Input the unique `Vendor ID` / `Receiver Code` (from Blinkit Category Manager) into `qc_platform_credentials`.
2. Register the middleware's webhook URL with Blinkit:
   ```
   https://api.dwarikas.com/api/v1/quickcommerce/blinkit/webhook/po/
   ```
3. Submit a configuration request to the Blinkit engineering team to enable webhook routing.
4. Configure the pincode routing map in `qc_warehouse_mappings`:
   ```json
   { "560067": "BLR_03", "500076": "HYD_02" }
   ```

### Step 3: Pre-Flight Validation Engine Deployment

Configure the middleware validation engine to enforce:
- FSSAI license check for F&B products
- EAN-13 barcode check-digit validation
- Image resolution and background compliance check
- MRP parity check (platform listing vs. physical label)
- MOQ check (≥ 500 units for JioMart initial stock)
- Shelf life check (≥ 60% remaining for JioMart perishables)

### Step 4: End-to-End Verification

1. **JioMart Sync Test:** Trigger a mock batch catalog creation request. Receive `trace_id`. Verify polling service tracks request to `COMPLETED` status without field-level errors.
2. **Blinkit PO Test:** Simulate an incoming Blinkit PO webhook. Verify order maps to target facility via pincode routing. Confirm that closing the manifest generates a valid ASN record.
3. **MRP Block Test:** Submit a Blinkit PO with mismatched MRP — verify fulfillment is blocked and an alert is raised in the Admin UI.

---

## 13. Environment Variables Required

| Variable | Description |
|---|---|
| `FYND_ACCESS_TOKEN` | Fynd Konnect OAuth access token |
| `FYND_USERNAME` | Fynd platform username from extension settings |
| `FYND_API_BASE_URL` | Fynd Konnect gateway base URL (e.g., `https://fyndkonnect.konnect.uat.fyndx1.de`) |
| `BLINKIT_VENDOR_ID` | Registered Blinkit Vendor ID / Receiver Code |
| `BLINKIT_WEBHOOK_SECRET` | Secret for validating incoming Blinkit webhook signatures |
| `BLINKIT_WEBHOOK_URL` | Public webhook URL registered with Blinkit engineering |
| `JIOMART_OAUTH_CLIENT_ID` | JioCommerce OAuth 2.0 Client ID |
| `JIOMART_OAUTH_CLIENT_SECRET` | JioCommerce OAuth 2.0 Client Secret |
| `JIOMART_MARKETPLACE_ID` | JioMart marketplace identifier |
| `QC_CATEGORY_MANAGER_EMAIL` | Blinkit Category Manager email for template routing |

---

## 14. Django App Structure

```
quickcommerce/
├── __init__.py
├── apps.py
├── models.py                   # QCPlatformListing, QCPlatformCredentials,
│                               # QCWarehouseMapping, QCPurchaseOrder, QCPOLineItem
├── serializers.py              # Listing sync, status, PO, ASN, metrics serializers
├── views.py                    # All API views (sync trigger, status, webhooks, metrics)
├── urls.py                     # /quickcommerce/ URL routing
├── validation_engine.py        # Pre-flight validation rules (FSSAI, EAN, MRP, MOQ)
├── schema_normalizer.py        # Unified master schema → platform-specific field mapping
├── pipelines/
│   ├── __init__.py
│   ├── jiomart_pipeline.py     # Fynd Konnect batch catalog creation + polling loop
│   └── blinkit_pipeline.py    # EAN/UPC match → SKU link OR template compilation
├── fulfillment/
│   ├── __init__.py
│   ├── jiomart_fulfillment.py  # Order fetch, manifest closure, returns sync
│   └── blinkit_fulfillment.py  # PO processing, MRP validation, ASN generation
├── metrics/
│   ├── __init__.py
│   └── calculator.py           # OTIF, Fill Rate, IDM computation from DB
└── tests/
    ├── test_validation.py       # Pre-flight validation rules
    ├── test_jiomart_pipeline.py # Batch catalog creation, polling, order lifecycle
    ├── test_blinkit_pipeline.py # EAN match, template compilation, PO webhook, ASN
    └── test_metrics.py          # OTIF / FR / IDM computation accuracy
```

---

## 15. Compliance Safeguards

### JioMart Compliance

| Rule | Detail |
|---|---|
| **Minimum Order Quantity (MOQ)** | ≥ 500 units per SKU for initial stock shipments |
| **Shelf Life** | ≥ 60% of total shelf life remaining at time of dark store delivery for perishables |
| **Barcoding** | Scannable EAN-13 barcodes compliant with JioMart packaging guidelines |

### Blinkit Compliance

| Rule | Detail |
|---|---|
| **Exact MRP Allocation** | MRP printed on physical label must exactly match MRP in PO |
| **Fulfillment Block** | System pauses fulfillment flow automatically if MRP mismatch detected |
| **Order Splitting** | Allowed; managed by the middleware |

---

## 16. Acceptance Criteria

- [ ] A manager can trigger a one-click listing for any product on both platforms and receive a combined status response within 2.5 seconds.
- [ ] The pre-flight validation engine correctly blocks submissions with invalid EAN-13 barcodes, missing FSSAI licenses (F&B), and MRP mismatches.
- [ ] JioMart: The batch POST to Fynd Konnect returns a `trace_id`; the polling service tracks the batch to `COMPLETED` status; field-level errors are stored in `validation_issues`.
- [ ] Blinkit (Match Path): When the product's EAN-13 already exists on Blinkit, the system links the seller's SKU directly to the Blinkit UPC and sets `sync_status` to `ACTIVE`.
- [ ] Blinkit (Template Path): When the SKU is new, the system compiles a compliant CSV/Excel template and routes it to the Category Manager; `sync_status` is set to `PENDING_REVIEW`.
- [ ] Blinkit PO webhook correctly resolves delivery pincode to a warehouse facility code via the mapping table.
- [ ] MRP mismatch on an incoming Blinkit PO triggers a fulfillment block and Admin UI alert.
- [ ] ASN submission correctly updates `qc_purchase_orders.po_status` to `ASN_SENT` and transmits the ASN to Blinkit.
- [ ] JioMart manifest closure transitions order status to `Dispatched` and notifies JioMart.
- [ ] OTIF, Fill Rate, and IDM metrics are correctly computed and exposed via the metrics API endpoint.
- [ ] All endpoints enforce `IsStaffOrManager` RBAC — customers cannot trigger listing or fulfillment actions.
- [ ] Full test coverage for validation engine, both pipeline branches, fulfillment flows, and metrics computation.
