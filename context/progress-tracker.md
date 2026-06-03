# Progress Tracker

Update this file after every meaningful implementation change.

## Current Phase

- Spec #08: Order Confirmation & Atomic Stock Decrement — **Complete**

## Current Goal

- Implement Barcode Generation Endpoints (Spec #09)


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



- None.

## Next Up

### Full Feature Spec Roadmap (Specs 04–19)

| Spec | Feature | Django App | Status |
|------|---------|------------|--------|
| 04 | Database Models & Schema | `inventory/` | ✅ Complete |
| 05 | RBAC Permissions | `api/` | ✅ Complete |
| 06 | Product Catalog API | `inventory/` | ✅ Complete |
| 07 | Checkout Reservation & Pessimistic Locking | `inventory/` | ✅ Complete |
| 08 | Order Confirmation & Atomic Stock Decrement | `inventory/` | ✅ Complete |
| 09 | Barcode Generation Endpoints | `inventory/` | 🔲 Not started |
| 09b | Loose Product Repackaging & Packet Barcode Creation | `inventory/` | 🔲 Not started — spec written 2026-06-03 — **depends on Spec 09** |
| 10 | Invoice Upload & Cloud Tasks Dispatch | `inventory/` | 🔲 Not started |
| 11 | Document AI OCR Worker | `tasks/` | 🔲 Not started |
| 12 | HITL Invoice Validation & Confirmation | `inventory/` | 🔲 Not started |
| 13 | ONDC Seller Node (Beckn Protocol) | `ondc/` | 🔲 Not started |
| 14 | WhatsApp Commerce Engine | `whatsapp/` | 🔲 Not started |
| 15 | External Partner API Gateway | `api/`, `inventory/` | 🔲 Not started |
| 16 | Security Hardening & Rate Limiting | `api/` | 🔲 Not started |
| 17 | Payment Gateway Integration (Razorpay) | `payments/` | 🔲 Not started — spec written 2026-06-03 |
| 18 | POS Cash Sales & In-Store Bill Generation | `pos/` | 🔲 Not started — spec written 2026-06-03 |

**Next immediate step:** Execute Spec #09 — Barcode Generation Endpoints, then Spec #09b — Loose Product Repackaging.


## Open Questions

- Should we use gunicorn or another ASGI server (e.g., uvicorn) for async support?
- How should environment variables be sourced in local dev vs production?
- Do we need additional Django middleware for request logging and error handling?
- `CORS_ALLOW_ALL_ORIGINS = True` — when to restrict to explicit origin list?
- `DATABASE_URL` local dev fallback: should we provide a `.env.example` template?

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

## Session Notes

- Dependency versions pinned to stable releases compatible with Django 6.0.5
- Dockerfile includes health checks compatible with Cloud Run service monitoring
- Non-root appuser (UID 1000) for security compliance
- pyproject.toml uses uv package manager (present in workspace)
- Spec #02 references `core/settings.py` — adapted to `dwarikasbackend/settings.py`
  (the actual Django project package name). Module references updated accordingly
  (ROOT_URLCONF, WSGI_APPLICATION).
