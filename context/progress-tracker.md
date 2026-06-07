# Progress Tracker

Update this file after every meaningful implementation change.

## Current Phase
 
- Spec #16: Security Hardening & Rate Limiting — **Complete**
 
## Current Goal
 
- Implement Payment Gateway Integration (Razorpay) (Spec #17)
  - ⚠️ **Blocked on manual pre-requisites** — see `context/feature-spec/spec17-Payment_Gateway_Integration.md` § Pre-Requisites
  - KYC submission and API key generation must happen before any integration work begins
  - Razorpay KYC approval can take 2–5 business days


## Completed

- ✅ Spec #01 - Required Modules & Dockerfile
  - Added all required Google Cloud dependencies (Secret Manager, Tasks, Storage, Document AI)
  - Added cryptographic libraries (pyjwt[crypto], cryptography, psycopg2-binary)
  - Added CORS support (django-cors-headers)
  - Created multi-stage Dockerfile with builder and runtime stages
  - Optimized for Cloud Run deployment (port 8080, non-root user, health checks)
  - **Completed:** 2026-05-23

- ✅ Spec #02 - Database & App Configuration (`dwarikasbackend/settings.py`)
  - Stripped Django admin, sessions, messages, staticfiles — stateless JWT-only API
  - Wired `dj_database_url` for Supabase PostgreSQL connection pooling
    (`conn_max_age=600`, `conn_health_checks=True`)
  - Configured DRF with `SupabaseJWTAuthentication` + `IsAuthenticated` defaults
  - Added `SUPABASE_JWT_SECRET` env var loading (used in Spec #03 JWT middleware)
  - Configured CORS headers for all origins (tighten to explicit list in production)
  - All secrets sourced from environment variables (Cloud Secret Manager at runtime)
  - Created `api/` Django app with `ApiConfig` and `authentication.py` stub
  - Updated `dwarikasbackend/urls.py` — removed admin route, stubbed api/v1/ include
  - Added `dj-database-url>=2.1.0` to `pyproject.toml` dependencies
  - **Completed:** 2026-05-23T23:11:53+05:30

- ✅ Spec #03 - Custom Stateless Authentication (`api/authentication.py`)
  - Replaced stub with full `SupabaseJWTAuthentication.authenticate()` implementation
  - HS256 JWT decode via PyJWT against `SUPABASE_JWT_SECRET` (no network round-trip)
  - Header parsing with `split(" ", 1)` for safety; validates `Bearer` scheme
  - Guards against missing secret, expired tokens, invalid audience, and invalid signatures
  - Constructs ephemeral in-memory Django `User` (never persisted — true statelessness)
  - Attaches `user.role` from `app_metadata.role` (defaults to `"customer"`)
  - Attaches `user.supabase_payload` for downstream views needing extra Supabase claims
  - **Completed:** 2026-05-23T23:29:55+05:30

- ✅ Spec #04 - Database Models & Schema (`inventory/models.py`)
  - Created Django `inventory/` app and registered it in `INSTALLED_APPS`
  - Defined unmanaged ORM models mirroring the Supabase schema (`Profile`, `Product`, `ProductVariant`, `Reservation`, `Order`)
  - Configured `managed = False` for all models to prevent Django from running DDL
  - Setup local development database with schema from `system_design.md` on local Supabase stack
  - Refined models with Kirana-specific display fields (MRP, weight/volume packaging, net quantity, unit of measure, brand, categories, dietary tags, image URLs) and updated database and migration records
  - Generated and applied initial Django migration with `--fake-initial` against the local database
  - **Completed:** 2026-05-30T21:40:00+05:30

- ✅ Spec #05 - RBAC Permissions & Role-Based Access Control (`api/permissions.py`)
  - Created `IsManager`, `IsStaffOrManager`, and `IsOwnerOrStaff` permission classes
  - Extracted roles directly from in-memory ephemeral user role claims (populated by `SupabaseJWTAuthentication`)
  - Guaranteed zero database hits by executing permissions against user role attributes and token claims without DB access
  - Built comprehensive test suite in `api/tests/test_permissions.py` validating permissions using `assertNumQueries(0)`
  - **Completed:** 2026-05-30T22:55:00+05:30

- ✅ Spec #06 - Product Catalog API (`inventory/serializers.py`, `inventory/filters.py`, `inventory/views.py`, `inventory/urls.py`)
  - Added `django-filter>=24.0` dependency and configured it in settings
  - Configured REST framework settings with default search, filter, and ordering backends along with page-size 25 pagination
  - Created `ProductVariantSerializer`, `ProductSerializer` (with nested variants prefetch), and `ProductWriteSerializer` to cover all inventory and metadata fields (MRP, packaging dimensions, dietary tags, etc.)
  - Implemented public catalog query views (list, detail, variants) and RBAC-guarded mutations (create, update, delete)
  - Added product `id` (UUID key) filtering support on the product list endpoint and updated the feature specification
  - Added `authenticate_header` to `SupabaseJWTAuthentication` to return `401 Unauthorized` for unauthenticated requests
  - Fixed read-only property mutation bug on ephemeral `User` class for `is_authenticated`
  - Created a custom in-memory test runner `ManagedModelTestRunner` to build database tables for unmanaged models during test executions
  - Built full test suite in `inventory/tests/test_views.py` verifying anonymous reads, RBAC writes/deletes, filtering/searching (including product ID), and N+1 query prevention (asserting 3 queries total on listing)
  - **Completed:** 2026-05-30T23:58:00+05:30

- ✅ Spec #06 (Amended) - Multi-Category Product Catalog Extension (`inventory/models.py`, `inventory/serializers.py`, `inventory/filters.py`, `inventory/views.py`)
  - **Analysis:** Verified the existing catalog fields (size, color, brand, category, image_url, description) are already sufficient for clothing at the variant level; identified missing fields for apparel: `product_type` discriminator, `material`, `gender_target`, `fit_type`
  - Added `product_type` TEXT field to `Product` model with choices: `grocery`, `apparel`, `general` — defaults to `general`
  - Added apparel-specific fields to `Product`: `material` (fabric composition), `gender_target` (men/women/unisex/boys/girls/none), `fit_type` (Slim Fit, Regular Fit, etc.)
  - All new fields are nullable/defaulted — fully backward-compatible with existing grocery data
  - Updated `ProductSerializer` and `ProductWriteSerializer` to expose all new fields
  - Extended `ProductFilter` with `product_type`, `brand`, `category`, `gender_target` filters
  - Extended `ProductListCreateView.search_fields` to include `brand`, `description`, `material`
  - Created Django state migration `0002_product_multicategory_fields.py` (no DDL — models are unmanaged)
  - Created Supabase DDL migration `supabase/snippets/002_product_catalog_multicategory.sql` with `ALTER TABLE`, backfill `UPDATE`, CHECK constraints, and indexes on `product_type` and `gender_target`
  - Updated `context/feature-spec/spec06-Product_Catalog_API.md` — synced spec with actual implementation, added multi-category product type model documentation, expanded filter parameters table, and added apparel-specific acceptance criteria
  - **Completed:** 2026-06-01T23:12:33+05:30

- ✅ Spec #08 - Order Confirmation & Atomic Stock Decrement (`inventory/order_views.py`, `inventory/serializers.py`, `inventory/urls.py`)
  - Created `inventory/order_views.py` with three views:
    - `OrderConfirmView` — `POST /api/v1/orders/confirm/`: acquires locks on variant and reservation using a deadlock-free ordering (parent ProductVariant first, then Reservation), verifies no timeout/expiry, decrements stock atomically, completes reservation, and creates `Order` with GST computation.
    - `OrderListView` — `GET /api/v1/orders/`: lists caller's historical orders (owner-isolated) or all orders for staff/managers.
    - `OrderDetailView` — `GET /api/v1/orders/<uuid:id>/`: retrieves a single order with user isolation or staff RBAC.
  - Added `OrderSerializer` to `inventory/serializers.py`.
  - Added order confirmation, list, and detail routes to `inventory/urls.py`.
  - Built full test suite in `inventory/tests/test_orders.py` verifying authentication, validations, success paths with GST rounding, expiry guards, user isolation, stock guards, and transaction rollback integrity.
  - **Completed:** 2026-06-01T23:59:00+05:30

- ✅ Spec #07 - Checkout Reservation & Pessimistic Locking (`inventory/checkout_views.py`, `inventory/serializers.py`, `inventory/urls.py`)
  - Created `inventory/checkout_views.py` with three views:
    - `CheckoutReserveView` — `POST /api/v1/checkout/reserve/`: acquires `SELECT ... FOR UPDATE NOWAIT` on `ProductVariant`, computes ATP, creates 10-minute `Reservation`
    - `ReservationListView` — `GET /api/v1/checkout/reserve/list/`: returns caller's active non-expired holds with nested variant details
    - `ReservationReleaseView` — `DELETE /api/v1/checkout/reserve/<uuid>/`: releases active reservation (owner or staff)
  - Added `ReservationSerializer` to `inventory/serializers.py` with nested `ProductVariantSerializer`
  - Updated `inventory/urls.py` with three new checkout routes under `checkout/reserve/`
  - Entire checkout flow wrapped in `transaction.atomic()` with `select_for_update(nowait=True)` on both variant and active reservation rows
  - ATP computed as `physical_stock - SUM(active, non-expired reservations)` inside the lock
  - Returns 409 with `atp` and `requested` fields when stock insufficient; 503 on lock contention; 404/400 for bad input
  - Created comprehensive test suite in `inventory/tests/test_checkout.py` (21 tests):
    - Full input validation, auth, ATP calculation, expiry exclusion, release authorization tests
    - Concurrency test: 10 threads race for last 1 unit — exactly 1 succeeds (HTTP 201); rest get 409/503
    - Used `TransactionTestCase` for concurrency test so worker threads see committed setUp data
    - Added `connections.close_all()` in thread teardown to allow clean PostgreSQL test DB drop
  - All 21 tests pass against local Supabase PostgreSQL (port 54322)
  - **Completed:** 2026-06-01T23:58:00+05:30

- ✅ Spec #09 - Barcode Generation Endpoints (`inventory/barcode_service.py`, `inventory/barcode_views.py`, `inventory/urls.py`)
  - Added dependencies `python-barcode[images]` and `Pillow` to `pyproject.toml`
  - Created standalone service module `inventory/barcode_service.py` to generate Code 128 and EAN-13 barcodes
  - Created DRF `Code128BarcodeView` and `EAN13BarcodeView` under `inventory/barcode_views.py`
  - Enforced `IsStaffOrManager` permission checks on endpoints
  - Added input validation for EAN-13 numeric strings and query parameters handling for Code 128
  - Added routes to `inventory/urls.py`
  - Created extensive unit/integration tests verifying permissions, output image validation, invalid inputs, and independent function execution
  - **Completed:** 2026-06-06T17:42:00+05:30

- ✅ Spec #09b - Loose Product Repackaging & Packet Barcode Creation (`inventory/packaging_serializers.py`, `inventory/packaging_views.py`, `inventory/urls.py`)
  - Added new unmanaged Django models `PackagingJob` and `PackagingJobOutput`.
  - Added fields `is_loose_commodity` to `Product` and `net_weight_value` to `ProductVariant`.
  - Configured compatible unit choices merging legacy catalog items (`pcs`, `pack`) with bulk repackaging metrics.
  - Implemented `POST /api/v1/packaging-jobs/` endpoint with automatic variant generation, stock incrementing, barcode generation, and database transaction rollback integrity.
  - Implemented `GET /api/v1/packaging-jobs/` (paginated list) and `GET /api/v1/packaging-jobs/<uuid:id>/` (job detail) endpoints.
  - Added comprehensive test suite verifying RBAC validations, automatic barcode creation, list pagination, detail views, and reservation integration.
  - **Completed:** 2026-06-06T23:55:00+05:30
- ✅ Spec #10 - Invoice Upload & Cloud Tasks Dispatch (`inventory/gcs_service.py`, `inventory/tasks_service.py`, `inventory/invoice_views.py`, `inventory/urls.py`)
  - Created unmanaged Django models `PurchaseInvoice` and `InvoiceLineItem` and applied database schema.
  - Enabled local database RLS (Row Level Security) and added permissions validation.
  - Implemented GCS service helpers with a self-contained local filesystem upload and signed URL fallback.
  - Implemented Cloud Tasks helper with local thread-based asynchronous HTTP request worker simulation.
  - Added DRF views for secure upload, list, and detail query fetching of invoices with status updates and JWT guards.
  - Created a robust unit and integration testing suite validating size limits, format validation, permissions, and fallback mechanisms.
  - **Completed:** 2026-06-06T18:50:00+05:30

- ✅ Spec #11 - Document AI OCR Worker (`tasks/document_ai_service.py`, `tasks/image_preprocessing.py`, `tasks/views.py`, `tasks/urls.py`, `inventory/tasks_service.py`)
  - Created new Django app `tasks` and configured it in settings.
  - Implemented OpenCV image preprocessing pipeline (`preprocess_image`) with greyscale, Otsu binarisation, morphological noise reduction, and deskewing.
  - Implemented Google Document AI client integration with trained invoice schema parsing, confidence scoring (review threshold 0.90), and mock parser fallback for test environments.
  - Implemented Cloud Tasks handler `ProcessInvoiceTaskView` supporting GCS downloads, local fallback file reads (`file:///`), transactional updates (`transaction.atomic`), and idempotency checks.
  - Updated simulated local Cloud Tasks enqueuer thread to attach `X-CloudTasks-TaskName` header.
  - Built comprehensive unit and integration tests covering security, payload format, file download fallback, OpenCV prep, Document AI parsing, DB transactions, and failure state transitions.
  - **Completed:** 2026-06-06T20:35:00+05:30

- ✅ Spec #12 - HITL Invoice Validation & Confirmation (`inventory/hitl_views.py`, `inventory/urls.py`, `inventory/tests/test_hitl.py`)
  - Implemented `InvoiceReviewView` returning detailed invoice fields, line items, and a signed GCS URL for visualization.
  - Implemented `LineItemUpdateView` supporting manual corrections of SKU, quantity, unit price, and tax, while clearing `needs_review` flag and blocking edits on already confirmed invoices.
  - Implemented `InvoiceConfirmView` committing stock additions atomically using `django.db.transaction.atomic` for all matched SKUs, changing invoice status to `'confirmed'`, and logging unmatched items.
  - Built comprehensive unit and integration tests verifying permissions, validation flows, transaction integrity, and idempotency.
  - **Completed:** 2026-06-06T20:50:00+05:30

- ✅ Spec #13 - ONDC Seller Node (Beckn Protocol Integration) (`ondc/`)
  - Created new Django app `ondc` and configured it in settings.
  - Implemented Ed25519 request signature verification conforming to Beckn signature spec.
  - Implemented Beckn context and catalog payload builder helpers in `ondc/beckn_builder.py`.
  - Implemented asynchronous task queueing / dispatching mechanism using Google Cloud Tasks and thread-based local worker simulation.
  - Implemented core views for discovery (`/search`), selection (`/select`), fulfillment initialization (`/init`), payment confirmation (`/confirm`), status polling (`/status`), and order/reservation cancellation (`/cancel`).
  - Added comprehensive integration test suite covering signature verification, ATP calculations, tax calculations (local CGST/SGST vs interstate IGST), and inventory atomic mutations.
  - **Completed:** 2026-06-06T21:50:00+05:30
 
- ✅ Spec #14 - WhatsApp Commerce Engine (`whatsapp/`)
  - Created new Django app `whatsapp` and registered it in `INSTALLED_APPS`.
  - Implements rule-based intent classification for incoming messages mapping to: `catalog`, `stock_check`, `order_status`, `fallback`.
  - Implements WhatsApp Cloud API client helper wrapper for Meta's messaging gateway.
  - Implements action handlers querying database real-time stock ATP and formatting lists, text, and CTA button structures.
  - Implements `WhatsAppWebhookView` for Meta webhook challenge-response GET handshake and X-Hub-Signature-256 POST verification.
  - Built comprehensive unit and integration test suite (16 tests) verifying endpoint verifications, intent routing, and mock messaging.
  - **Completed:** 2026-06-06T22:45:00+05:30

- ✅ Spec #15 - External Partner API Gateway (`api/`, `inventory/`)
  - Created `public.external_api_keys` database table with manager-only RLS policy and `check_user_is_manager` security definer helper function.
  - Added `carrier_status` and `tracking_reference` tracking columns to `public.orders` and Django `Order` model.
  - Implemented `ExternalApiKeyAuthentication` checking `X-Dwarikas-Api-Key` and authenticating ephemeral `ExternalPartner` users.
  - Implemented `HasValidRequestSignature` executing replay protection checks and verifying HMAC-SHA256 request payload signatures using `EXTERNAL_SIGNING_SECRET`.
  - Created Django management command `create_api_key` to provision cryptographically secure API keys and output raw tokens once.
  - Developed administrative endpoints `AdminApiKeyListCreateView` and `AdminApiKeyRevokeView` for API key management.
  - Created `ExternalInventorySyncView` for batch stock delta updates and `ExternalShipmentUpdateView` to ingest fulfillment updates.
  - Written comprehensive test suite with 14 unit and integration tests covering security validations, delta calculations, and lifecycle endpoints.
  - **Completed:** 2026-06-06T23:30:00+05:30

- ✅ Spec #16 - Security Hardening & Rate Limiting (`api/middleware.py`, `api/logout_view.py`, `api/health_view.py`, `api/urls.py`, `api/tests/test_security.py`)
  - Configured Redis-backed cache with LocMemCache fallback for test runs.
  - Enabled global DRF throttling: anonymous requests at 60/min, authenticated requests at 300/min.
  - Integrated `django-axes` for brute-force protection (lockout after 5 failed attempts, custom 403 JSON lockout payload).
  - Added early `AxesLockoutMiddleware` to return custom JSON errors for blocked IPs and bypass health probes.
  - Implemented `JTIBlocklistMiddleware` and `revoke_jti` for real-time token/session revocation.
  - Implemented `UserAgentValidationMiddleware` to enforce device binding and prevent JWT replay attacks.
  - Added `GET /api/v1/health/` liveness check for database and cache connections.
  - Added `POST /api/v1/auth/logout/` token revocation endpoint.
  - Written 9 comprehensive unit and integration tests verifying all security checks.
  - **Completed:** 2026-06-06T23:58:00+05:30

## Next Up

### Full Feature Spec Roadmap (Specs 04–19)

| Spec | Feature | Django App | Status |
|------|---------|------------|--------|
| 04 | Database Models & Schema | `inventory/` | ✅ Complete |
| 05 | RBAC Permissions | `api/` | ✅ Complete |
| 06 | Product Catalog API | `inventory/` | ✅ Complete |
| 07 | Checkout Reservation & Pessimistic Locking | `inventory/` | ✅ Complete |
| 08 | Order Confirmation & Atomic Stock Decrement | `inventory/` | ✅ Complete |
| 09 | Barcode Generation Endpoints | `inventory/` | ✅ Complete |
| 09b | Loose Product Repackaging & Packet Barcode Creation | `inventory/` | ✅ Complete |
| 10 | Invoice Upload & Cloud Tasks Dispatch | `inventory/` | ✅ Complete |
| 11 | Document AI OCR Worker | `tasks/` | ✅ Complete |
| 12 | HITL Invoice Validation & Confirmation | `inventory/` | ✅ Complete |
| 13 | ONDC Seller Node (Beckn Protocol) | `ondc/` | ✅ Complete |
| 14 | WhatsApp Commerce Engine | `whatsapp/` | ✅ Complete |
| 15 | External Partner API Gateway | `api/`, `inventory/` | ✅ Complete |
| 16 | Security Hardening & Rate Limiting | `api/` | ✅ Complete |
| 17 | Payment Gateway Integration (Razorpay) | `payments/` | ⚠️ Pre-requisites pending — spec updated 2026-06-08 with KYC/webhook/secrets checklist |
| 18 | POS Cash Sales & In-Store Bill Generation | `pos/` | 🔲 Not started — spec written 2026-06-03 |
| 19 | Promotions & Discounts | `promotions/` | 🔲 Not started — spec written 2026-06-06 |
| 20 | Coupon Code Creation & Application | `coupons/` | 🔲 Not started — spec written 2026-06-06 |
| 21 | Gaming Engine Integration & Coupon Rewards | `gaming/` | 🔲 Not started — spec written 2026-06-06 |
| 22 | Smart Discount Suggestions Engine | `promotions/` | 🔲 Not started — spec written 2026-06-07 |
| 23 | Amazon SP-API One-Click Product Listing | `amazon/` | 🔲 Not started — spec written 2026-06-07 |
| 24 | Blinkit & JioMart One-Click Product Listing | `quickcommerce/` | 🔲 Not started — spec written 2026-06-07 |
| 24b | Local Frontend–Backend Integration Test | frontend + all apps | 🔲 Not started — spec written 2026-06-07 |
| 25 | Cloud Run Deployment Readiness | infra / all apps | 🔲 Not started — spec written 2026-06-07 |
| 26 | Production Deployment Runbook (CI/CD) | infra | 🔲 Planned — to be written after Spec #25 |

**Next immediate step:** Execute Spec #17 — Payment Gateway Integration (Razorpay).


## Open Questions

- Should we use gunicorn or another ASGI server (e.g., uvicorn) for async support?
- How should environment variables be sourced in local dev vs production?
- Do we need additional Django middleware for request logging and error handling?
- `CORS_ALLOW_ALL_ORIGINS = True` — when to restrict to explicit origin list?
- `DATABASE_URL` local dev fallback: should we provide a `.env.example` template?
- **[Spec 17]** Fee handling strategy: should Dwarikas absorb the ~2% gateway fee on card/net-banking transactions, or pass it to customers as a `convenience_fee` line item? UPI is 0% (RBI mandate) so this only affects card/net-banking. Options:
  - Option A: Absorb silently (current spec behaviour — simplest UX)
  - Option B: Pass full fee to customer with RBI-mandated pre-payment disclosure
  - Option C: Hybrid — absorb UPI (already free), charge convenience fee for card/net-banking only
- **[Spec 17]** Webhook URL for Razorpay registration: confirm the final Cloud Run domain once deployed so the webhook can be registered with the correct URL.

## Architecture Decisions

- **Multi-stage Docker Build**: Used builder stage to compile native dependencies
  (psycopg2, cryptography) and kept runtime stage lightweight for faster deployment
  to Cloud Run. This reduces final image size and attack surface.
- **Python 3.14-slim Base**: Selected for minimal footprint while supporting all
  required packages. Slim variant removes build tools from final image.
- **Gunicorn for WSGI**: Chosen for production-grade request handling with
  configurable workers for concurrency.
- **Stateless Settings (Spec #02)**: Removed `django.contrib.admin`, sessions,
  messages, and staticfiles from INSTALLED_APPS. The API is JWT-authenticated and
  stateless; no server-side sessions or admin UI are served by this container.
- **dj_database_url (Spec #02)**: Chosen to parse `DATABASE_URL` (Supabase
  PostgreSQL connection string) into Django's DATABASES dict. `conn_max_age=600`
  keeps pooled connections alive across Cloud Run execution bursts.
- **api/ app (Spec #02)**: Created a dedicated `api/` Django application as the
  primary REST gateway, separate from the auto-generated `backend/` scaffold app.
  The `backend/` app can be repurposed or removed in a future cleanup step.
- **Razorpay as payment gateway (Spec #17)**: Evaluated Razorpay vs Cashfree.
  Razorpay retained for three reasons: (1) it is the most-documented ONDC-approved
  Payment Aggregator — critical since Spec #13 ONDC `/confirm` routes through the
  same payment layer; (2) UPI dominates Indian retail (0% fee on both gateways by
  RBI mandate), neutralizing Cashfree's marginal fee advantage; (3) Spec 17 is
  already written for Razorpay's exact API shape — migration would be net-zero benefit.
  Cashfree to be reconsidered if Dwarikas launches a multi-vendor marketplace
  requiring high-frequency seller payouts.
- **Payment amounts computed server-side only (Spec #17)**: All order amounts
  (subtotal, GST, and any future convenience fees) are computed from the database
  in the backend. The frontend sends only `reservation_id` and `payment_method_type`.
  This prevents client-side amount tampering and upholds the atomic stock-payment
  invariant established in Spec #08.

## Session Notes

- Dependency versions pinned to stable releases compatible with Django 6.0.5
- Dockerfile includes health checks compatible with Cloud Run service monitoring
- Non-root appuser (UID 1000) for security compliance
- pyproject.toml uses uv package manager (present in workspace)
- Spec #02 references `core/settings.py` — adapted to `dwarikasbackend/settings.py`
  (the actual Django project package name). Module references updated accordingly
  (ROOT_URLCONF, WSGI_APPLICATION).
