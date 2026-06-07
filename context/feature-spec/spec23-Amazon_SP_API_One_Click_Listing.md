# Spec #23 — Amazon SP-API One-Click Product Listing Integration

**Project:** Dwarika's Omnichannel Tech Ecosystem  
**Target Capability:** Real-time One-Click Product Listing on Amazon Marketplaces  
**Primary Integration:** Amazon Selling Partner API (SP-API) – Listings Items v2021-08-01  
**Django App:** `amazon/`

---

## 1. Executive Summary & Objective

The objective of this module is to enable Dwarika's store administrators to list any catalog product on Amazon Marketplaces (such as Amazon.in) with a single click from the administrative dashboard.

To achieve sub-second responsiveness, the system uses the modern, **synchronous** Amazon SP-API Listings Items API (v2021-08-01) rather than the legacy bulk XML/Flat-File Feeds API. While the Feeds API is asynchronous and slow (often taking 15 minutes to hours), the Listings Items API validates and accepts/rejects individual submissions in real-time, providing immediate frontend feedback with downstream, event-driven status tracking.

### Key Performance & Architectural Metrics

| Metric | Target |
|---|---|
| **Frontend Submission Delay** | < 250 ms (backend validates and registers intent instantly before kicking off the API call) |
| **Synchronous Feedback Loop** | < 2.5 seconds — structural validations (missing fields, format mismatches) |
| **Asynchronous Feedback Loop** | Catalog sync, brand verification, search suppression tracked via Amazon SNS/SQS webhooks |
| **Rate-Limiting Resilience** | Handles default 5 RPS limit per seller using token buckets and exponential backoff queues |

---

## 2. High-Level Architecture Diagram

```
+---------------------------------------------------------------------------------------------------------+
|                                        DWARIKA'S ECOSYSTEM BOUNDARY                                     |
|                                                                                                         |
|   +-------------------+          +------------------------+          +-------------------------+        |
|   |  Next.js Admin UI | <======> | Django / Rust Backend  | <======> |  Supabase PostgreSQL    |        |
|   | (Click to Publish)|          | (Business Orchestrator)|          | (Products & Sync States)|        |
|   +-------------------+          +------------------------+          +-------------------------+        |
|             ^                                |                                    ^                     |
|             | Polling / SSE                  | POST /listings/items               | Saves Token / State |
|             v                                v                                    v                     |
|   +-------------------+          +------------------------+                                             |
|   |  API Endpoint for |          | Login with Amazon (LWA)|                                             |
|   |  Real-time Status |          | Auth Handler (Tokens)  |                                             |
|   +-------------------+          +------------------------+                                             |
|                                              |                                                          |
+----------------------------------------------|----------------------------------------------------------+
                                               | TLS v1.3 HTTPS
                                               v
                             +-----------------------------------+
                             |     Amazon SP-API Gateway         |
                             |  (https://sellingpartnerapi-eu...)|
                             +-----------------------------------+
                                               |
                                               | Pushes Event Status Changes
                                               v
+---------------------------------------------------------------------------------------------------------+
|                                        AWS INFRASTRUCTURE CLOUD                                         |
|                                                                                                         |
|   +------------------------------------+          +-------------------------------------------------+   |
|   |  Amazon SNS Topic                  | ======>  |  Amazon SQS Queue (Webhook Receiver Queue)      |   |
|   | (LISTINGS_ITEM_STATUS_CHANGE)      |          |  (Triggers webhook call back to Dwarika's API)  |   |
|   +------------------------------------+          +-------------------------------------------------+   |
+---------------------------------------------------------------------------------------------------------+
```

---

## 3. Prerequisites & Amazon Seller Setup

Before executing API requests, Dwarika's developer account must be provisioned and authorized:

### 3.1 Developer Profile & Role Registration
- Register a Developer Profile in the Amazon Seller Central Partner Network.
- Request and secure approval for the **Product Listing role** in your developer profile.

### 3.2 IAM AWS Configuration
- Create an AWS IAM User or Role with policies allowing `execute-api:Invoke` on SP-API endpoints, or configure AWS Signature Version 4.
- Bind access keys for AWS SQS/SNS integrations.

### 3.3 Application Registration
- Register your application in Seller Central.
- Retrieve your **Client ID** (LWA Client ID) and **Client Secret** (LWA Client Secret).

### 3.4 Selling Partner Authorization (LWA OAuth 2.0 Flow)
- Provide a redirect URI pointing back to the admin page:  
  `https://admin.dwarikas.com/integrations/amazon/callback`
- Once the seller grants access, Amazon redirects to this URI with an authorization code.
- The backend exchanges this authorization code for an **LWA Refresh Token** via:

  ```
  POST https://api.amazon.com/auth/o2/token
  ```

  This Refresh Token is permanent (unless revoked) and is stored securely in Supabase.

---

## 4. Database Schema Design (Supabase PostgreSQL)

### 4.1 Amazon Credentials Table

Stores active Selling Partner keys, refresh tokens, and authorized marketplace metadata.

```sql
CREATE TYPE amazon_region_enum AS ENUM ('NA', 'EU', 'FE');

CREATE TABLE amazon_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id VARCHAR(255) NOT NULL UNIQUE,
    lwa_client_id VARCHAR(255) NOT NULL,
    lwa_client_secret TEXT NOT NULL,
    lwa_refresh_token TEXT NOT NULL,
    region amazon_region_enum NOT NULL DEFAULT 'EU',
    primary_marketplace_id VARCHAR(50) NOT NULL,
    authorized_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Enable Row Level Security (RLS)
ALTER TABLE amazon_credentials ENABLE ROW LEVEL SECURITY;

-- Allow only platform Admins to read/modify credentials
CREATE POLICY admin_full_access ON amazon_credentials
    FOR ALL USING (auth.jwt() ->> 'role' = 'admin');
```

### 4.2 Amazon Listings Sync Table

Maps Dwarika's core products to their respective Amazon listings and active sync states.

```sql
CREATE TYPE listing_sync_status_enum AS ENUM (
    'PENDING', 'SUBMITTED', 'ACTIVE', 'INVALID', 'ERROR', 'SUPPRESSED'
);

CREATE TABLE amazon_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sku VARCHAR(100) NOT NULL UNIQUE,           -- Matches core product SKU
    asin VARCHAR(10) NULL,                      -- Populated upon successful listing matching
    marketplace_id VARCHAR(50) NOT NULL,
    sync_status listing_sync_status_enum NOT NULL DEFAULT 'PENDING',
    submission_id UUID NULL,                    -- Matches SP-API submissionId from putListingsItem
    validation_issues JSONB DEFAULT '[]'::jsonb, -- Stores synchronous/asynchronous errors
    price_synced NUMERIC(10, 2) NULL,
    quantity_synced INTEGER DEFAULT 0,
    last_synced_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Indexes for fast lookups
CREATE INDEX idx_amazon_listings_sku ON amazon_listings(sku);
CREATE INDEX idx_amazon_listings_product ON amazon_listings(product_id);

ALTER TABLE amazon_listings ENABLE ROW LEVEL SECURITY;

-- Staff and Admin can manage listings tracking
CREATE POLICY staff_admin_listing_access ON amazon_listings
    FOR ALL USING (auth.jwt() ->> 'role' IN ('admin', 'staff'));
```

---

## 5. The "One-Click" Listing Pipeline

```
+------------+     1. Trigger Click     +-----------------+
| Admin UI   | ----------------=======> | Backend API     |
+------------+                          +-----------------+
      ^                                          |
      | 4. Poll Status                           | 2. Auth & Put (Synchronous)
      |                                          v
+----------------------+                +-----------------+
| Supabase DB          | <============= | Amazon SP-API   |
| (amazon_listings)    |  Update Status +-----------------+
+----------------------+                         |
      ^                                          | 3. Event Notification (Asynchronous)
      |                                          v
      +--------------------------------- +-----------------+
               Update State via Webhook  | AWS SQS Webhook |
                                         +-----------------+
```

### Phase 1: Local Pre-Flight Parsing & Target Mapping

When the operator clicks the "List on Amazon" button:

1. Retrieve core attributes from `products` table: `name`, `description`, `brand`, `barcode` (EAN/UPC), `price`, `stock counts`.
2. Lookup product type mapping (e.g., if Category is `'Groceries'`, map to SP-API product type `GROCERY` or generic `PRODUCT` for offer-only listing).
3. **Offer-Only Listing:** If the product barcode (EAN/UPC) already exists on Amazon, attach our price and stock to an existing ASIN.  
   **Full Catalog Listing:** If the barcode does not exist, submit a new full catalog entry.

### Phase 2: Request Token Acquisition (LWA)

The backend retrieves an ephemeral Access Token:

**Endpoint:** `POST https://api.amazon.com/auth/o2/token`

**Payload:**
```json
{
  "grant_type": "refresh_token",
  "refresh_token": "Atzr|...",
  "client_id": "amzn1.application-oa2-client.foo",
  "client_secret": "xyz123abc"
}
```

**Response:** Returns an `access_token` valid for **3,600 seconds**.  
The backend caches this token in-memory (or Redis) and reuses it until within 60 seconds of expiry.

### Phase 3: Synchronous API Invocation (putListingsItem)

**Endpoint:**
```
PUT https://sellingpartnerapi-eu.amazon.com/listings/2021-08-01/items/{sellerId}/{sku}
    ?marketplaceIds={marketplaceId}
```

**Required Headers:**
```
x-amzn-AccessToken: {LWA_ACCESS_TOKEN}
Content-Type: application/json
```

**Complete putListingsItem Request Payload:**
```json
{
  "productType": "PRODUCT",
  "requirements": "LISTING",
  "attributes": {
    "item_name": [
      {
        "value": "Premium Organic Basmati Rice - 5kg",
        "language_tag": "en_IN",
        "marketplace_id": "A21TJRUUN4KGV"
      }
    ],
    "brand": [
      {
        "value": "Dwarika's Farms",
        "language_tag": "en_IN",
        "marketplace_id": "A21TJRUUN4KGV"
      }
    ],
    "externally_assigned_product_identifier": [
      {
        "type": "ean",
        "value": "8901234567890",
        "marketplace_id": "A21TJRUUN4KGV"
      }
    ],
    "merchant_suggested_asin": [
      {
        "value": "B01NXYZ123",
        "marketplace_id": "A21TJRUUN4KGV"
      }
    ],
    "purchasable_offer": [
      {
        "marketplace_id": "A21TJRUUN4KGV",
        "currency": "INR",
        "our_price": [
          {
            "schedule": [
              {
                "value_with_tax": 499.00
              }
            ]
          }
        ]
      }
    ],
    "fulfillment_availability": [
      {
        "fulfillment_channel_code": "DEFAULT",
        "quantity": 120,
        "marketplace_id": "A21TJRUUN4KGV"
      }
    ],
    "condition_type": [
      {
        "value": "new_new",
        "marketplace_id": "A21TJRUUN4KGV"
      }
    ],
    "bullet_point": [
      {
        "value": "Pure long grain organic Basmati rice naturally aged for 2 years.",
        "language_tag": "en_IN",
        "marketplace_id": "A21TJRUUN4KGV"
      }
    ]
  }
}
```

**Synchronous Response Scenarios:**

**Scenario A — Success (Status `ACCEPTED`):**
```json
{
  "sku": "DWRK-RICE-ORG-05",
  "status": "ACCEPTED",
  "submissionId": "7df4e528-9844-46ab-8991-6cf1b54c86e2",
  "issues": []
}
```
> **Action:** Update `amazon_listings.sync_status` to `SUBMITTED`.

**Scenario B — Immediate Rejection (Status `INVALID`):**
```json
{
  "sku": "DWRK-RICE-ORG-05",
  "status": "INVALID",
  "submissionId": "7df4e528-9844-46ab-8991-6cf1b54c86e2",
  "issues": [
    {
      "code": "90220",
      "message": "The field 'externally_assigned_product_identifier' format is invalid for type 'ean'.",
      "severity": "ERROR",
      "attributeNames": ["externally_assigned_product_identifier"]
    }
  ]
}
```
> **Action:** Update `amazon_listings.sync_status` to `INVALID`, save error arrays in `validation_issues`, display corrected payload suggestion in the UI.

### Phase 4: Event-Driven Downstream Tracking (AWS SQS Webhook)

- Amazon processes listing matching, pricing rules, and brand policy checks offline.
- Amazon fires `LISTINGS_ITEM_STATUS_CHANGE` and `LISTINGS_ITEM_ISSUES_CHANGE` notifications to the configured AWS SNS Topic.
- The SNS topic routes messages to a dedicated Amazon SQS Queue.
- Dwarika's backend polls the SQS Queue (or handles message pushes via a serverless function/webhook listener) to update the state of the listing in Supabase to `ACTIVE`, `SUPPRESSED`, or `ERROR`.

---

## 6. API Specification

### 6.1 Trigger Sync to Amazon

| Property | Value |
|---|---|
| **URL** | `POST /api/v1/amazon/listings/sync/` |
| **Auth** | `IsStaffOrManager` (Supabase JWT with `staff` or `manager` role) |

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
| `404` | Product UUID not found in Dwarika's catalog |
| `422` | Amazon SP-API returned `INVALID` status — issues returned in body |
| `429` | SP-API rate limit exceeded; retry after backoff |
| `502` | Amazon SP-API unreachable or returned 5xx |

---

### 6.2 Listing Status Check (Polling API)

| Property | Value |
|---|---|
| **URL** | `GET /api/v1/amazon/listings/{product_id}/status/` |
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
| `SUBMITTED` | Submission accepted by Amazon; awaiting catalog processing |
| `ACTIVE` | Live and buyable on Amazon marketplace |
| `INVALID` | Rejected due to structural validation errors |
| `SUPPRESSED` | Active but hidden from search due to a policy issue |
| `ERROR` | Unexpected error; check `issues` array for details |

---

### 6.3 SQS Webhook Receiver Endpoint

| Property | Value |
|---|---|
| **URL** | `POST /api/v1/amazon/webhooks/sqs-receiver/` |
| **Auth** | Internal — verified via Amazon SNS signature headers |

**Request Body (Amazon SNS Notification Envelope):**
```json
{
  "Type": "Notification",
  "MessageId": "8c35e679-b1d6-512c-91bc-0e19488a0b0f",
  "TopicArn": "arn:aws:sns:eu-west-1:123456789012:DwarikasAmazonListings",
  "Message": "{\"notificationType\":\"LISTINGS_ITEM_STATUS_CHANGE\",\"payload\":{\"sellerId\":\"A3ABCDEFOOBAR\",\"sku\":\"DWRK-BASMATI-5K\",\"marketplaceId\":\"A21TJRUUN4KGV\",\"status\":\"BUYABLE\",\"asin\":\"B01NXYZ123\"}}"
}
```

**Behaviour:**
- Parse the inner `Message` JSON string.
- Map `"BUYABLE"` → `ACTIVE`, `"SUPPRESSED"` → `SUPPRESSED`, etc.
- Update `amazon_listings` row where `sku` matches.
- If `asin` is provided, populate the `asin` column.
- Return `200 OK` to prevent Amazon SNS retry loops.

---

## 7. Rate Limiting & Retry Strategy

| Strategy | Detail |
|---|---|
| **Token Bucket** | Maintain a local token bucket allowing 5 requests/sec per seller account |
| **Exponential Backoff** | On `429` or `503` from SP-API: retry after 1s, 2s, 4s, 8s (max 4 retries) |
| **Access Token Cache** | Cache LWA access token in Redis; refresh 60 seconds before 3600s expiry |
| **Idempotency** | Before submitting, check if `amazon_listings` row already has `sync_status = SUBMITTED` or `ACTIVE` to prevent duplicate submissions |

---

## 8. Environment Variables Required

| Variable | Description |
|---|---|
| `AMAZON_LWA_CLIENT_ID` | OAuth2 client ID from Amazon Developer Portal |
| `AMAZON_LWA_CLIENT_SECRET` | OAuth2 client secret |
| `AMAZON_LWA_REFRESH_TOKEN` | Long-lived refresh token from seller authorization |
| `AMAZON_SELLER_ID` | Amazon Seller Central seller ID |
| `AMAZON_MARKETPLACE_ID` | Default marketplace ID (e.g., `A21TJRUUN4KGV` for Amazon.in) |
| `AMAZON_SP_API_BASE_URL` | Regional base URL (e.g., `https://sellingpartnerapi-eu.amazon.com`) |
| `AMAZON_SNS_TOPIC_ARN` | ARN of the SNS topic receiving Amazon listing notifications |
| `AWS_SQS_QUEUE_URL` | URL of the SQS queue backing the SNS topic |

---

## 9. Django App Structure

```
amazon/
├── __init__.py
├── apps.py
├── models.py               # AmazonCredentials, AmazonListing (unmanaged ORM)
├── serializers.py          # AmazonListingSyncSerializer, AmazonListingStatusSerializer
├── views.py                # AmazonListingSyncView, AmazonListingStatusView, SQSWebhookView
├── urls.py                 # /amazon/ URL routing
├── lwa_client.py           # LWA token acquisition & caching
├── sp_api_client.py        # putListingsItem HTTP wrapper with retry/backoff
├── sqs_processor.py        # SQS message parsing & status update logic
└── tests/
    └── test_amazon.py      # Tests for sync trigger, status polling, webhook receiver
```

---

## 10. Acceptance Criteria

- [ ] A manager can click "List on Amazon" for any product in the catalog and receive a `SUBMITTED` or `INVALID` response within 2.5 seconds.
- [ ] The `amazon_listings` table is created/updated correctly for both `ACCEPTED` and `INVALID` SP-API responses.
- [ ] The `validation_issues` JSONB field stores all returned SP-API error codes for display in the Admin UI.
- [ ] The SQS webhook receiver correctly maps `BUYABLE` to `ACTIVE` and populates the `asin` column.
- [ ] Rate limiting: 6th concurrent request within 1 second is queued and retried, not dropped.
- [ ] LWA access tokens are cached and not re-fetched on every listing submission.
- [ ] All endpoints enforce `IsStaffOrManager` RBAC — customers cannot trigger listing actions.
- [ ] Full test coverage for sync trigger, status polling, webhook parsing, and RBAC enforcement.
