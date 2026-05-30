# Progress Tracker

Update this file after every meaningful implementation change.

## Current Phase

- Spec #05: RBAC Permissions & Role-Based Access Control (Complete)

## Current Goal

- Implement Product Catalog API (Spec #06)

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

## In Progress

- None.

## Next Up

### Full Feature Spec Roadmap (Specs 04–16)

| Spec | Feature | Django App | Status |
|------|---------|------------|--------|
| 04 | Database Models & Schema | `inventory/` | ✅ Complete |
| 05 | RBAC Permissions | `api/` | ✅ Complete |
| 06 | Product Catalog API | `inventory/` | 🔲 Not started |
| 07 | Checkout Reservation & Pessimistic Locking | `inventory/` | 🔲 Not started |
| 08 | Order Confirmation & Atomic Stock Decrement | `inventory/` | 🔲 Not started |
| 09 | Barcode Generation Endpoints | `inventory/` | 🔲 Not started |
| 10 | Invoice Upload & Cloud Tasks Dispatch | `inventory/` | 🔲 Not started |
| 11 | Document AI OCR Worker | `tasks/` | 🔲 Not started |
| 12 | HITL Invoice Validation & Confirmation | `inventory/` | 🔲 Not started |
| 13 | ONDC Seller Node (Beckn Protocol) | `ondc/` | 🔲 Not started |
| 14 | WhatsApp Commerce Engine | `whatsapp/` | 🔲 Not started |
| 15 | External Partner API Gateway | `api/`, `inventory/` | 🔲 Not started |
| 16 | Security Hardening & Rate Limiting | `api/` | 🔲 Not started |

**Next immediate step:** Execute Spec #06 — Product Catalog API.

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
