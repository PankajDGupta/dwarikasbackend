# Dwarikas Integrated Omnichannel Retail Portal
## Technical Architecture & Implementation Blueprint

---

## 1. Project Overview & Business Mandate
The modern retail ecosystem in the Indian market requires a tight integration of physical storefronts and digital channels, creating a cohesive "phygital" retail network. For an enterprise like Dwarikas, bridging the gap between digital and physical commerce requires a central system that acts as the single source of truth for all inventory, transactions, and customer interactions. 

The Dwarikas Integrated Retail Portal is explicitly designed to address key retail operational bottlenecks, such as the **"ghost inventory"** problem—where products are erroneously shown as available online but are physically missing from store shelves, or vice versa.

### Strategic Key Performance Indicators (KPIs)
To measure, monitor, and guarantee the success of this infrastructure, the implementation is governed by high-performance KPIs mapped directly to the core strategic goals of the enterprise:

| Strategic Pillar | Core Technical Objective | Key Performance Indicator (KPI) | Target Completion Metric |
| :--- | :--- | :--- | :--- |
| **Operational Excellence** | Eliminate manual inventory overhead | 85% reduction in stock entry processing time | 95% line-item extraction accuracy via AI OCR/IDP validation |
| **Customer-Centricity** | Provide a seamless omnichannel experience | 30% increase in cross-channel repeat purchases | Unified session and cart state across Next.js Web and Mobile Apps |
| **Data-Driven Growth** | Gain unified transaction visibility | 99.9% inventory data accuracy rate across channels | 0% occurrences of ghost inventory sales |
| **Scalability & Performance** | Support multi-store deployment with sub-second responsiveness | Sub-300ms API response time at peak concurrency | Less than 2 seconds for thermal barcode label generation and print execution |

---

## 2. System Architecture & Tech Stack
To achieve deep operational cohesion, the technical stack completely decouples the user experiences from the business logic and database layers:
* **Frontend Client:** Next.js for high-performance, search-engine-optimized client web interfaces, administrative portals, and a dedicated mobile application for native customer experiences.
* **Backend & Orchestration:** Django REST Framework (DRF) serving as the central business logic, orchestration engine, and stateless API gateway.
* **Database & Auth Layer:** Supabase providing a real-time managed PostgreSQL database and JWT-based authentication layer.

### Infrastructure Topology
The core systems are deployed as independent containerized services on Google Cloud Platform (GCP):

    ┌────────────────────────────────────────────────────────┐
    │                      User Client                       │ 
    └───────────────────────────┬────────────────────────────┘
                                │ HTTPS via GCLB
                                ▼
    ┌────────────────────────────────────────────────────────┐
    │              Google Cloud Load Balancer                │
    └──────────────┬───────────────────────────┬─────────────┘
                                │
                                ▼                           ▼
    ┌─────────────────────────────┐   ┌─────────────────────────────┐
    │    Cloud Run Service A      │   │    Cloud Run Service B      │
    │  [Next.js Client/Admin UI]  │   │   [Django REST API Gateway] │
    └─────────────────────────────┘   └─────────────┬───────────────┘
    │
    ┌────────────────┬───────────────────────┼────────────────┐
    ▼                ▼                       ▼                ▼
    ┌──────────────┐┌──────────────┐        ┌────────────────┐┌──────────────┐
    │Secret Manager││Cloud Storage │        │  Cloud Tasks   ││ Supabase DB  │
    │(Keys/Secrets)││(Scanned Bills)        │ (Buffer Queue) ││(Postgres/WAL)│
    └──────────────┘└──────────────┘        └────────────────┘└──────────────┘

### Stateless Service Architecture and Scale-to-Zero Metrics
* **Next.js Frontend Service:** Packaged with a lightweight Node.js server container optimized for Server-Side Rendering (SSR) and Edge Caching. It scales dynamically from zero to meet active user traffic.
* **Django REST Framework API Service:** Packaged with an enterprise WSGI/ASGI server (e.g., Gunicorn/Uvicorn). It functions as the stateless gateway for business logic, processing incoming API requests from the Web, Mobile, POS, and external partner networks.
* **Cost & Concurrency Optimization:** Both services leverage Cloud Run's scale-to-zero setting, de-provisioning container instances during off-peak hours (such as late-night store closures) while scaling up instantly to handle high concurrency during promotional campaigns.

### Production Environment Database Routing
The Django application connects seamlessly to Supabase PostgreSQL using Unix domain sockets when running in the production Google Cloud Run environment to minimize latency and overhead, while defaulting to standard TCP/IP connections during local development via the Cloud SQL Auth Proxy or a local database instance.

### GCP Infrastructure Blueprint & Resource Configurations
| GCP Resource | Configuration Specification | Operational Purpose | IAM Role / Access Control |
| :--- | :--- | :--- | :--- |
| Google Artifact Registry | Docker Format, Regional Repository (e.g., asia-south1) | Immutable storage for versioned container images | roles/artifactregistry.writer for Cloud Build;roles/artifactregistry.reader for Cloud Run |
| Cloud Run (Next.js Service) | CPU: 1 vCPU, Memory: 2GiB, Min Instances: 0, Max: 10, Scaling: Concurrency-based (80) | Serving customer e-commerce and admin web interfaces | Public access allowed; authenticated to API via Supabase Auth JWT |
| Cloud Run (Django Service) | CPU: 2 vCPU, Memory: 4GiB, Min Instances: 0, Max: 20, Always-Allocated CPU: Enabled | Executing business logic, OCR processing, and external API gateways | Public access allowed; communicates with Secret Manager, GCS, and Supabase DB |
| Cloud Run Jobs | Run-to-completion, shared Django container image | Executing schema migrations and administrative CLI routines without race conditions | Triggered by Cloud Build during deployment pipelines |
| Cloud Storage (GCS) | Standard storage class, Uniform Bucket-Level Access, Private | Secure storage of raw invoice PDFs, JPEGs, and static media | roles/storage.objectAdmin for the Django Service Account |
| Cloud Tasks | Regional Queue, Max Dispatches: 10/sec, Max Concurrent: 5 | Buffering and dispatching OCR invoice processing requests asynchronously | roles/cloudtasks.enqueuer for Django API;roles/run.invoker for target workers |
| Secret Manager | Secret: django_settings, replication: automatic | Secure injection of runtime environment parameters and Supabase credentials | roles/secretmanager.secretAccessor granted to Cloud Run and Cloud Build Service Accounts |






# CI/CD Deployment Automation (cloudbuild.yaml)
All database migrations (python manage.py migrate) are strictly isolated into run-to-completion Cloud Run Jobs to prevent multi-instance race conditions and avoid startup latency overhead.


# cloudbuild.yaml
steps:    
  # Step 1: Build the Django container image from source    
  - name: 'gcr.io/cloud-builders/docker'      
    id: 'build-django-image'      
    args:        
      - 'build'        
      - '-t'        
      - 'asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:$COMMIT_SHA'        
      - '-f'        
      - './backend/Dockerfile'       
      - './backend'    
      
  # Step 2: Push the built image to Google Artifact Registry    
  - name: 'gcr.io/cloud-builders/docker'      
    id: 'push-django-image'      
    args:        
      - 'push'        
      - 'asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:$COMMIT_SHA'    
      
  # Step 3: Run database migrations using a dedicated Cloud Run Job    
  - name: 'gcr.io/[google.com/cloudsdktool/cloud-sdk](https://google.com/cloudsdktool/cloud-sdk)'      
    id: 'execute-db-migrations' 
    entrypoint: 'gcloud'      
    args:        
      - 'run'        
      - 'jobs'        
      - 'execute'        
      - 'dwarikas-migrate-job'        
      - '--region=asia-south1'        
      - '--wait'    
      
  # Step 4: Deploy the updated image to the stateless Cloud Run Service    
  - name: 'gcr.io/[google.com/cloudsdktool/cloud-sdk](https://google.com/cloudsdktool/cloud-sdk)'      
    id: 'deploy-stateless-api'      
    entrypoint: 'gcloud'      
    args:        
      - 'run'        
      - 'deploy'        
      - 'dwarikas-django-api'        
      - '--image=asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:$COMMIT_SHA'        
      - '--region=asia-south1'        
      - '--platform=managed'    
      - '--service-account=cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com'        
      - '--update-secrets=DATABASE_URL=django_settings:latest,JWT_SECRET_KEY=django_settings:latest'  

images:    
  - 'asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:$COMMIT_SHA'


# 3. Database Architecture & Security Protocols
## Unified Database Schema (Supabase / PostgreSQL)
```sql
-- Profiles table for user metadata and Role-Based Access Control (RBAC)
CREATE TABLE public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('customer', 'staff', 'manager')),
    phone_number TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Master products table storing catalog metadata  
CREATE TABLE public.products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    hsn_code TEXT NOT NULL,
    gst_slab NUMERIC(5, 2) NOT NULL DEFAULT 18.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Variant configuration with physical stock mapping  
CREATE TABLE public.product_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    sku TEXT UNIQUE NOT NULL,
    barcode TEXT UNIQUE,
    size TEXT,
    color TEXT,
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    retail_price NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Active checkout inventory holds  
CREATE TABLE public.reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    reserved_quantity INTEGER NOT NULL CHECK (reserved_quantity > 0),
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'expired')) DEFAULT 'active',
    effective_price NUMERIC(12, 2),
    promotion_id UUID REFERENCES public.promotions(id) ON DELETE SET NULL
);

-- Complete order transaction logs  
CREATE TABLE public.orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    total_amount NUMERIC(12, 2) NOT NULL,
    gst_amount NUMERIC(12, 2) NOT NULL,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('UPI', 'card', 'cash', 'online')),  -- 'online' added in Spec #17 for Razorpay-managed flows
    payment_status TEXT NOT NULL CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded')),  -- 'refunded' added in Spec #17
    carrier_status TEXT CHECK (carrier_status IN ('staged', 'picked_up', 'in_transit', 'delivered')),  -- added in Spec #15
    tracking_reference TEXT,  -- added in Spec #15
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Payment transaction audit log (Spec #17 — Razorpay)
-- Separate from orders to allow multiple payment attempts per reservation
CREATE TABLE public.payment_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reservation_id UUID REFERENCES public.reservations(id) ON DELETE SET NULL,
    order_id UUID REFERENCES public.orders(id) ON DELETE SET NULL,
    user_id UUID NOT NULL,
    razorpay_order_id TEXT UNIQUE NOT NULL,       -- rp_order_XXXXXX
    razorpay_payment_id TEXT,                     -- pay_XXXXXX (filled after payment)
    razorpay_signature TEXT,                      -- HMAC-SHA256 (filled after verify)
    amount_paise INTEGER NOT NULL,                -- Amount in paise (1 INR = 100 paise)
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL DEFAULT 'created'
              CHECK (status IN ('created', 'attempted', 'paid', 'failed', 'refunded')),
    failure_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_txn_rp_order ON public.payment_transactions(razorpay_order_id);
CREATE INDEX IF NOT EXISTS idx_payment_txn_user ON public.payment_transactions(user_id);

-- POS Carts table (Spec #18)
CREATE TABLE IF NOT EXISTS public.pos_carts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    staff_user_id   UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    customer_phone  TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'confirmed', 'abandoned')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pos_cart_staff ON public.pos_carts(staff_user_id);

-- POS Cart line items (Spec #18)
CREATE TABLE IF NOT EXISTS public.pos_cart_items (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id     UUID NOT NULL REFERENCES public.pos_carts(id) ON DELETE CASCADE,
    variant_id  UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(12, 2) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cart_id, variant_id)
);

-- Order line items (Spec #18)
CREATE TABLE IF NOT EXISTS public.order_items (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id                UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
    variant_id              UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    sku_snapshot            TEXT NOT NULL,
    product_name_snapshot   TEXT NOT NULL,
    hsn_code_snapshot       TEXT NOT NULL,
    gst_slab_snapshot       NUMERIC(5, 2) NOT NULL,
    quantity                INTEGER NOT NULL CHECK (quantity > 0),
    unit_price              NUMERIC(12, 2) NOT NULL,
    subtotal                NUMERIC(12, 2) NOT NULL,
    gst_amount              NUMERIC(12, 2) NOT NULL,
    line_total              NUMERIC(12, 2) NOT NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON public.order_items(order_id);

-- promotions table
CREATE TABLE public.promotions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    description         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      NUMERIC(10, 2) NOT NULL,
    max_discount_cap    NUMERIC(10, 2),
    min_order_value     NUMERIC(10, 2),
    banner_image_url    TEXT,
    starts_at           TIMESTAMPTZ NOT NULL,
    ends_at             TIMESTAMPTZ,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- promotion_items table
CREATE TABLE public.promotion_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promotion_id    UUID NOT NULL REFERENCES public.promotions(id) ON DELETE CASCADE,
    product_id      UUID REFERENCES public.products(id) ON DELETE CASCADE,
    variant_id      UUID REFERENCES public.product_variants(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_product_or_variant CHECK (
        product_id IS NOT NULL OR variant_id IS NOT NULL
    )
);

-- promotion_broadcasts table
CREATE TABLE public.promotion_broadcasts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promotion_id    UUID NOT NULL REFERENCES public.promotions(id) ON DELETE CASCADE,
    phone_number    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
    failure_reason  TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_by         UUID NOT NULL
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_promotions_active_dates ON public.promotions (is_active, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_promotion_items_product ON public.promotion_items (product_id);
CREATE INDEX IF NOT EXISTS idx_promotion_items_variant ON public.promotion_items (variant_id);
CREATE INDEX IF NOT EXISTS idx_promotion_broadcasts_promotion ON public.promotion_broadcasts (promotion_id);

-- Create coupons table (Spec #20)
CREATE TABLE IF NOT EXISTS public.coupons (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                TEXT UNIQUE NOT NULL,   -- Always stored UPPERCASE
    description         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      DECIMAL(10, 2) NOT NULL,
    max_discount_cap    DECIMAL(10, 2),
    min_order_value     DECIMAL(10, 2),
    max_uses            INTEGER,                -- NULL = unlimited
    uses_per_user       INTEGER NOT NULL DEFAULT 1,
    specific_user_id    UUID,                   -- NULL = public coupon
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_until         TIMESTAMPTZ,            -- NULL = no expiry
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source              TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'gaming_reward', 'referral'))
);

-- Create coupon redemptions audit log (Spec #20)
CREATE TABLE IF NOT EXISTS public.coupon_redemptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coupon_id           UUID NOT NULL REFERENCES public.coupons(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL,
    order_id            UUID REFERENCES public.orders(id) ON DELETE SET NULL,
    reservation_id      UUID REFERENCES public.reservations(id) ON DELETE SET NULL,
    discount_applied    DECIMAL(12, 2) NOT NULL,
    redeemed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extend reservations table with coupon columns (Spec #20)
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS coupon_id       UUID REFERENCES public.coupons(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS coupon_discount DECIMAL(12, 2),
    ADD COLUMN IF NOT EXISTS final_price     DECIMAL(12, 2);

-- Create performance indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_coupons_code ON public.coupons (UPPER(code));
CREATE INDEX IF NOT EXISTS idx_coupons_specific_user ON public.coupons (specific_user_id) WHERE specific_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_user ON public.coupon_redemptions (user_id);
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_coupon ON public.coupon_redemptions (coupon_id);

-- Reward tiers (manager-configured prize table) (Spec #21)
CREATE TABLE IF NOT EXISTS public.reward_tiers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    game_type           TEXT NOT NULL,
    win_level           TEXT NOT NULL,         -- 'any' acts as wildcard
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      DECIMAL(10, 2) NOT NULL,
    max_discount_cap    DECIMAL(10, 2),
    valid_days          INTEGER NOT NULL DEFAULT 30,
    description         TEXT,
    notify_whatsapp     BOOLEAN NOT NULL DEFAULT TRUE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Game plays (one row per play consumed — tracks quota) (Spec #21)
CREATE TABLE IF NOT EXISTS public.game_plays (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    game_session_id     TEXT UNIQUE NOT NULL,   -- Unity GUID — deduplication key
    game_type           TEXT NOT NULL,
    play_source         TEXT NOT NULL DEFAULT 'order' CHECK (play_source IN ('order', 'rewarded_ad')),
    played_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Game rewards (win events + issued coupons — audit log) (Spec #21)
CREATE TABLE IF NOT EXISTS public.game_rewards (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    game_play_id        UUID UNIQUE NOT NULL REFERENCES public.game_plays(id) ON DELETE CASCADE,
    game_type           TEXT NOT NULL,
    win_level           TEXT NOT NULL,
    reward_tier_id      UUID REFERENCES public.reward_tiers(id) ON DELETE SET NULL,
    coupon_id           UUID UNIQUE REFERENCES public.coupons(id) ON DELETE SET NULL,
    whatsapp_sent       BOOLEAN NOT NULL DEFAULT FALSE,
    whatsapp_delivered  BOOLEAN,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Performance indexes for gaming (Spec #21)
CREATE INDEX IF NOT EXISTS idx_game_plays_user     ON public.game_plays  (user_id);
CREATE INDEX IF NOT EXISTS idx_game_plays_session  ON public.game_plays  (game_session_id);
CREATE INDEX IF NOT EXISTS idx_game_rewards_user   ON public.game_rewards (user_id);
CREATE INDEX IF NOT EXISTS idx_reward_tiers_lookup ON public.reward_tiers (game_type, win_level) WHERE is_active = TRUE;

-- Discount suggestions table (Spec #22)
CREATE TABLE IF NOT EXISTS public.discount_suggestions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id                  UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE CASCADE,
    product_id                  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    discount_score              INTEGER NOT NULL,
    priority                    TEXT NOT NULL CHECK (priority IN ('critical', 'high', 'medium')),
    reason_summary              TEXT NOT NULL,
    reasons                     JSONB NOT NULL DEFAULT '{}',
    suggested_discount_type     TEXT NOT NULL CHECK (suggested_discount_type IN ('percentage', 'flat_amount')),
    suggested_discount_value    DECIMAL(10, 2) NOT NULL,
    suggested_ends_days         INTEGER NOT NULL DEFAULT 14,
    current_stock               INTEGER NOT NULL,
    avg_monthly_sales           DECIMAL(10, 2) NOT NULL DEFAULT 0,
    days_since_last_order       INTEGER,
    cost_price                  DECIMAL(12, 2),
    margin_pct                  DECIMAL(5, 2),
    status                      TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'dismissed', 'expired')),
    dismissed_until             TIMESTAMPTZ,
    approved_promotion_id       UUID REFERENCES public.promotions(id) ON DELETE SET NULL,
    analysed_at                 TIMESTAMPTZ NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One active suggestion per variant
CREATE UNIQUE INDEX IF NOT EXISTS idx_discount_suggestion_active_variant
    ON public.discount_suggestions (variant_id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_discount_suggestions_status_score
    ON public.discount_suggestions (status, discount_score DESC);
```,StartLine:206,TargetContent:
```

### Row Level Security (RLS) Structural Mitigations
#### Enforcing authorization controls within the PostgreSQL engine via Supabase RLS requires navigating specific architectural gotchas:
| Security Risk / Gotcha| Impact Severity| Underlying Mechanism| Required Structural Mitigation|
|---|---|---|---|
|Service Role Key Abuse|Critical|Initializing client SDK instances using the service_role key bypasses all RLS checks, rendering the database fully exposed.|Restrict service_role usage strictly to backend tasks running inside Django; never expose it to client code.|
|User Metadata Tampering|High|Reading permissions from auth.jwt() -> 'user_metadata' allows clients to edit their own roles via malicious client-side API requests.|Write authorization data and roles to raw_app_meta_data, which cannot be modified by client requests.|
|Recursive Policy Crashes|High|Policies that perform subqueries referencing the host table create infinite execution loops, leading to database timeouts.|Implement wrapper helper functions defined with SECURITY DEFINER to bypass host-table evaluations.|
|Anon Role Leakage|Medium|Unauthenticated requests map to the default anon role, exposing data if policies do not check explicit permissions.|Define policies explicitly with TO authenticated to block unauthorized access early in evaluation.|


### Security Definer Recursion Mitigation Helper

```sql
CREATE OR REPLACE FUNCTION public.check_user_is_staff(user_uuid UUID) 
RETURNS BOOLEAN AS $$ 
BEGIN      
    RETURN EXISTS (         
        SELECT 1 FROM public.profiles         
        WHERE id = user_uuid            
          AND role IN ('staff', 'manager')     
    ); 
END; 
$$ LANGUAGE plpgsql SECURITY DEFINER;
```



---

## 4. Local Development Environment & Tooling

### Supabase Local Stack

The project runs a **local Supabase Docker instance** during development. All database-related tasks (schema changes, data inspection, ad-hoc queries) in the local environment **must use the Supabase CLI** — do not use raw `psql` or Django migrations for DDL against this local stack.

#### Running Services

| Service | URL / Connection |
|---|---|
| **PostgreSQL DB** | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` |
| **REST API** | `http://127.0.0.1:54321/rest/v1` |
| **Studio (UI)** | `http://127.0.0.1:54323` |
| **Auth / API** | `http://127.0.0.1:54321` |

Verify the stack is up at any time with:
```powershell
supabase status
```

#### Supabase CLI — Database Commands

All local database operations are executed via the Supabase CLI from the project root (`dwarikasbackend/`):

```powershell
# Run a single SQL statement
supabase db query "SELECT * FROM public.products LIMIT 5;"

# Inspect a table's schema
supabase db query "SELECT column_name, data_type, column_default, is_nullable
                   FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'products'
                   ORDER BY ordinal_position;"

# Apply a DDL change (one statement per call)
supabase db query "ALTER TABLE public.products ADD COLUMN IF NOT EXISTS my_col TEXT;"
```

> **Important:** `supabase db query` executes **one SQL statement per call**. Multiple semicolon-separated statements in a single call will fail with `SQLSTATE 42601`. Always split multi-statement migrations into individual `supabase db query` calls.

#### SQL Snippets Folder

Schema migrations that are not managed by Django's migration system (i.e., Supabase DDL not covered by `manage.py migrate`) are stored as numbered SQL files in:

```
supabase/snippets/
    001_initial_schema.sql        ← initial Supabase schema (profiles, products, etc.)
    002_product_catalog_multicategory.sql  ← multi-category product type extension
```

These snippets document every DDL change applied to the local and production Supabase databases. When implementing a new spec that requires schema changes:
1. Write the DDL as a new numbered snippet in `supabase/snippets/`
2. Apply each statement individually using `supabase db query "..."`
3. Verify the schema with a `SELECT ... FROM information_schema.columns` query
4. Record the applied migration in `context/progress-tracker.md`

#### Django Migration Strategy for Unmanaged Models

All inventory ORM models (`Product`, `ProductVariant`, `Reservation`, `Order`) are declared with `managed = False`. This means:

- **Django never runs DDL** (no `CREATE TABLE`, `ALTER TABLE`) against Supabase
- `python manage.py migrate` only updates Django's internal migration state
- **All DDL runs through the Supabase CLI** using the snippets above
- Django migrations for unmanaged models serve as state-only records of the schema shape

---

## 5. Operational Ingestion & Hardware Integration

### Intelligent Document Processing (IDP) Bill Ingestion

To eliminate manual inventory data-entry overhead, vendor invoice processing is offloaded to an asynchronous computer vision pipeline:

    ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
    │  Upload UI   │ ───► │  GCS Bucket  │ ───► │ Cloud Tasks  │ ───► │  Django REST │
    │  (Next.js)   │      │ (Raw Image)  │      │   (Buffer)   │      │ (OpenCV Prep)│
    └──────────────┘      └──────────────┘      └──────────────┘      └──────┬───────┘
                                                                            │
                                                                            ▼
    ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
    │  Supabase    │ ◄─── │ Human-in-the-│ ◄─── │  Candidate   │ ◄─── │  AI OCR/LLM  │
    │  (Confirmed) │      │  Loop (HITL) │      │  Extraction  │      │  Extraction  │
    └──────────────┘      └──────────────┘      └──────────────┘      └──────────────┘

Ingestion: Staff photograph invoices using the Next.js Admin UI; files are pushed directly to a private GCS bucket.

Asynchronous Orchestration: Django registers the upload event and pushes a lightweight metadata payload to a Google Cloud Tasks queue to avoid blocking main web threads.

Computer Vision Preprocessing: Django worker instances execute OpenCV routines: Binarization (color channel reduction to enhance text contrast), Deskewing (correcting physical alignment offsets up to 15°), and Noise Reduction (smudge/artifact filtering).

Contextual LLM Extraction: Preprocessed images pass through an IDP API (e.g., Nanonets / Document AI) utilizing Large Language Models to contextually parse variable structured templates.

Schema Validation Mapping:

Vendor Name $\rightarrow$ products.brand_id (Flags unknown entities via active registers).

GSTIN $\rightarrow$ vendor_profiles.gstin (Validates structural syntax e.g., 27AAAAA1111A1Z1).

Invoice Number $\rightarrow$ purchase_invoices.invoice_no (Prevents duplicate ingestion).

Invoice Date $\rightarrow$ purchase_invoices.issued_at (Parses standard ISO strings into UTC).

Line Items (SKU, Qty, Cost, GST) $\rightarrow$ Maps calculations directly:
$$\text{Total Amount} = (\text{Quantity} \times \text{Unit Price}) + \text{Tax}$$

Human-in-the-Loop (HITL): Extraction fields scoring under a 90% confidence score are highlighted red in the Next.js Admin UI alongside the original image for manual confirmation prior to Supabase commit.




## Barcode Engineering & Thermal Printing Layout
Every unique SKU is tracked using a standard dual system: Code 128 for internal inventory batches and EAN-13 for standard retail items.

```python
import barcode 
from barcode.writer import ImageWriter 
from io import BytesIO 

def generate_internal_barcode(sku: str, batch_id: str) -> BytesIO:      
    """    
    Generates a Code 128 barcode image buffer containing     
    sku and batch identifiers for internal tracking.    
    """     
    sanitized_sku = "".join(c for c in sku if c.isalnum())    
    sanitized_batch = "".join(c for c in batch_id if c.isalnum())    
    payload = f"{sanitized_sku}-{sanitized_batch}"         
    
    code128 = barcode.get_by_name('code128')    
    buffer = BytesIO()        
    writer_options = {         
        'module_height': 15.0,         
        'module_width': 0.2,         
        'quiet_zone': 2.0,         
        'write_text': True,         
        'text_distance': 4.0    
    }        
    barcode_instance = code128(payload, writer=ImageWriter())    
    barcode_instance.write(buffer, options=writer_options)    
    buffer.seek(0)     
    return buffer
```


### Hardware Zebra Programming Language (ZPL) Spec
The Next.js client interacts with local thermal printers (Zebra/TSC) directly via Web Bluetooth or Serial API using raw ZPL streams:

```
^XA^FX Set label boundaries and media defaults^LH0,0^PW400^LL200^PR3,3^MD15
^FX Brand Header and Product Name^FO20,20^A0N,28,28^FDDWARIKAS^FS
^FO20,50^A0N,18,18^FDHandcrafted Silk Scarf^FS
^FX Dynamic Retail Price Configuration^FO20,80^A0N,24,24^FDRs. 2,499.00^FS
^FO20,105^A0N,14,14^FD(Inclusive of 18% GST)^FS
^FX Code 128 Barcode Generation^BY2,2,40^FO20,130^BCN,45,Y,N,N^FDHS102-B2026^FS^XZ
``` 

### 5. Omnichannel Transactional Integrity & Availability Guards

Available-to-Promise (ATP) Concurrency Model
To eliminate risk profiles associated with concurrent checkout overselling across Next.js Web, Native Mobile, and physical POS scans, the data layer enforces a pessimistic dual-phase validation model. ATP stock is derived dynamically via:

$$\text{ATP} = \text{Physical Stock Quantity} - \sum \text{Active Reservations}$$

Where $\sum \text{Active Reservations}$ represents rows in the reservations table where status = 'active' and expires_at > now().Django DRF Pessimistic Concurrency GuardPythonfrom django.db import transaction, DatabaseError 

```python
from rest_framework.views import APIView 
from rest_framework.response import Response 
from rest_framework import status 
from datetime import datetime, timezone, timedelta 
from .models import ProductVariant, Reservation 

class ProcessSecureCheckout(APIView):      
    """    
    Enforces atomic transactional updates on physical database records    
    to prevent overselling and race conditions across channels.    
    """      
    def post(self, request, format=None):          
        variant_id = request.data.get("variant_id")        
        purchase_qty = int(request.data.get("quantity", 1))        
        user_id = request.user.id          
        
        try:             
            # Wrap database operations in an atomic transaction block              
            with transaction.atomic():                 
                # Acquire a pessimistic lock on the variant row                 
                variant = ProductVariant.objects.select_for_update().get(id=variant_id)                          
                
                # Fetch all active reservations for this variant locked via select_for_update
                active_reservations = Reservation.objects.filter(                    
                    variant_id=variant_id,                    
                    status='active',                    
                    expires_at__gt=datetime.now(timezone.utc)                
                ).select_for_update()                     
                
                total_reserved = sum(res.reserved_quantity for res in active_reservations)                
                atp_inventory = variant.stock_quantity - total_reserved                                 
                
                # Check if there is enough Available-to-Promise stock                 
                if atp_inventory < purchase_qty:                     
                    return Response(                        
                        {"error": "Insufficient inventory available"},                        
                        status=status.HTTP_409_CONFLICT                 
                    )                                 
                
                # Create a temporary reservation to hold the items for 10 minutes                
                Reservation.objects.create(                    
                    variant_id=variant.id,                    
                    user_id=user_id,                    
                    reserved_quantity=purchase_qty,                    
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),                    
                    status='active'                 
                )               
                return Response({"status": "Reservation lock successful"}, status=status.HTTP_201_CREATED)                     
        
        except ProductVariant.DoesNotExist:             
            return Response({"error": "Variant SKU not found"}, status=status.HTTP_404_NOT_FOUND)         
        except DatabaseError:             
            return Response({"error": "Database lock timeout, retry"}, status=status.HTT
```



### Pure Database-Level Atomic Modification Flow
```SQL
-- Step 1: Initialize transaction block  
BEGIN; 

-- Step 2: Acquire pessimistic locks on target rows  
SELECT id, stock_quantity  
FROM public.product_variants  
WHERE id = '4f8d6892-d698-4228-a40d-277cb76131c2'   
FOR UPDATE;

-- Step 3: Perform atomic inline update checking inventory bounds bounds 
UPDATE public.product_variants 
SET stock_quantity = stock_quantity - 2  
WHERE id = '4f8d6892-d698-4228-a40d-277cb76131c2'    
  AND stock_quantity >= 2;

-- Step 4: Commit transaction and release locks  
COMMIT;
```
Note: Successful updates are broadcast immediately to all connected edge clients using Supabase's real-time engine tracking PostgreSQL Write-Ahead Logs (WAL).



# 6. External Market Integrations & Edge Security

## ONDC Beckn Asynchronous Network Protocol

Integrating as an ONDC Seller Node requires mapping asynchronous callbacks (on_ endpoints) via an enterprise adapter architecture (e.g., NSDL eGov Adapter / Beckn ONIX):

```
ONDC Network          Dwarikas Seller Node (DRF)           Supabase DB
    │                             │                             │
    │  1. /search (Async)         │                             │
    ├────────────────────────────►│                             │
    │                             │  2. Calculate ATP           │
    │                             ├────────────────────────────►│
    │                             │◄────────────────────────────┤
    │  3. /on_search (Callback)   │                             │
    │◄────────────────────────────┤                             │
    │                             │                             │
    │  4. /select (Async)         │                             │
    ├────────────────────────────►│                             │
    │                             │  5. Validate Stock          │
    │                             ├────────────────────────────►│
    │                             │◄────────────────────────────┤
    │  6. /on_select (Callback)   │                             │
    │◄────────────────────────────┤                             │

```

Discovery (/search $\rightarrow$ /on_search): Evaluates local catalog search requests against real-time ATP constraints.

Selection (/select $\rightarrow$ /on_select): Verifies item details, delivery conditions, and creates a temporary database reservation lock.

Fulfillment Init (/init $\rightarrow$ /on_init): Performs local GST routing logic (CGST, SGST, IGST calculations based on HSN taxonomy).

Fulfillment Confirm (/confirm $\rightarrow$ /on_confirm): Commits the transaction and converts the reservation into a finalized logged order block.


### ONDC Adapter Parameter Configuration Matrix

    subscriber_id: Unique network URI identifier for the Dwarikas Node in the ONDC registry.

    keyid: Resolves the specific public key used to verify incoming network payload signatures.

    private_key: Base64 string used to generate cryptographic signatures for outgoing responses.

    certificate: Base64 .p12 container handling mutual TLS (mTLS) registry handshakes.

    schemav2validator: Core validation plugin matching data payloads to structural Beckn specifications.

    ehcache: Cache duration constraint (integer hours) to minimize registry lookup overheads.


### WhatsApp Business Conversational Commerce Engine

    Maps real-time messaging interactions to structured database queries:

    Request Ingestion: Direct incoming messaging JSON webhooks are processed by the Django application.

    Intent Parsing: A menu-driven message handler maps chat queries to automated catalog or routing systems.

    Database Lookup: Queries Supabase to fetch accurate real-time stock levels and order tracking details.

    Interactive Formatting: Constructs outbound JSON payloads using WhatsApp list pickers and call-to-action (CTA) button arrays to allow smooth in-app store lookups.




## Security Engineering and API Gateway Architecture

For corporate ERPs and external logistic sync integrations, edge security protocols are enforced strictly:


    External Partner             API Gateway (Django DRF)              PostgreSQL DB
        │                                │                             │
        │ 1. POST /sync with Signature   │                             │
        ├───────────────────────────────►│                             │
        │                                │ 2. Verify Key Hash          │
        │                                ├────────────────────────────►│
        │                                │ 3. Verify Signature & Auth  │
        │                                │◄────────────────────────────┤
        │                                ├─┐                           │
        │                                │ │                           │
        │                                |◄┘                           │
        │                                │ 4. Complete Action          │
        │                                ├────────────────────────────►│

- API Key Hashing: External tokens are exposed to clients only once upon generation; the database stores a secure one-way SHA-256 key hash.


- Cryptographic Request Signing: Incoming gateway traffic requires an X-Dwarikas-Signature header calculated using an HMAC-SHA256 signature generated across a structured data payload:$$\text{HMAC-SHA256}(\text{Secret Key}, \text{Request Method} + \text{Path} + \text{Timestamp} + \text{Request Body})$$ 


- Device Binding Protection: Middleware parses JWT authentication tokens to ensure embedded login client user_agent strings exactly match the incoming HTTP User-Agent headers, completely mitigating token theft hijack loops.

```Python
# Django User-Agent JWT Validation Middleware  
from django.http import JsonResponse 
from rest_framework_simplejwt.tokens import AccessToken 
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError 

class UserAgentValidationMiddleware:      
    """    
    Validates that incoming HTTP requests carry a User-Agent header     
    matching the device claims embedded in the authenticated JWT.    
    """      
    def __init__(self, get_response):         
        self.get_response = get_response     
        
    def __call__(self, request):         
        auth_header = request.headers.get("Authorization")         
        if auth_header and auth_header.startswith("Bearer "):            
            raw_token = auth_header.split(" ")[1]             
            try:                 
                validated_token = AccessToken(raw_token)     
                token_user_agent = validated_token.get("user_agent")                
                current_user_agent = request.headers.get("User-Agent")                                 
                
                if token_user_agent and token_user_agent != current_user_agent:                     
                    return JsonResponse(                        
                        {"error": "Session token bound to a different device"},                         
                        status=403              
                    )             
            except (InvalidToken, TokenError):                 
                return JsonResponse({"error": "Invalid token signature"}, status=401)                         
        return self.get_response(request)
```

- Session Management & Hardening: Enforces unique active mapping variables tracking user_id $\rightarrow$ token identifiers (jti) inside Redis to allow global cross-device logout revocations.


- Brute-Force & Rate Limits: Integrates django-axes to drop brute-force IP sweeps after 5 sequential failures with a strict 2-hour sliding lock pattern. Employs memory-hard Argon2 criteria for baseline credential storage alongside strict Redis endpoint rate limits.

### Corporate OpenAPI 3.0.3 Gateway Integration Contract

```yaml
openapi: 3.0.3
info:
  title: Dwarikas External Integration Gateway
  version: 1.0.0
  description: Secured external integration API endpoints.
paths:
  /api/v1/external/inventory/sync:
    post:
      summary: Reconcile external ERP inventory levels with the central database.
      security:
        - ExternalApiKey: []
        - RequestSignature: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - timestamp
                - sync_items
              properties:
                timestamp:
                  type: string
                  format: date-time
                sync_items:
                  type: array
                  items:
                    type: object
                    required:
                      - sku
                      - quantity_delta
                    properties:
                      sku:
                        type: string
                      quantity_delta:
                        type: integer
      responses:
        '200':
          description: Inventory reconciliation completed successfully.
        '401':
          description: API credentials missing or invalid.
        '409':
          description: Inconsistent state or lock conflict during sync.
  /api/v1/external/shipments/update:
    patch:
      summary: Update shipping and fulfillment status for active orders.
      security:
        - ExternalApiKey: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - order_id
                - carrier_status
                - tracking_reference
              properties:
                order_id:
                  type: string
                  format: uuid
                carrier_status:
                  type: string
                  enum: [staged, picked_up, in_transit, delivered]
                tracking_reference:
                  type: string
      responses:
        '200':
          description: Shipment status updated and notifications triggered.
        '400':
          description: Invalid request payload.
components:
  securitySchemes:
    ExternalApiKey:
      type: apiKey
      in: header
      name: X-Dwarikas-Api-Key
      description: Raw API key validated against secure database hashes.
    RequestSignature:
      type: apiKey
      in: header
      name: X-Dwarikas-Signature
      description: Hex-encoded HMAC-SHA256 signature calculated over the request payload.

```

### Amazon SP-API One-Click Marketplace Listing

Store administrators can list any product from Dwarika's catalog directly onto Amazon Marketplaces (Amazon.in) with a single click. The system uses the synchronous SP-API Listings Items v2021-08-01 endpoint to provide real-time validation feedback and downstream event-driven status tracking.

```
 Dwarika Admin UI          Django Backend (amazon/)          Amazon SP-API
        │                           │                             │
        │  1. POST /listings/sync    │                             │
        ├────────────────────────►│                             │
        │                           │  2. POST /auth/o2/token      │
        │                           ├────────────────────────►│
        │                           │◄──────── access_token ─────▤
        │                           │  3. PUT /listings/items      │
        │                           ├────────────────────────►│
        │                           │◄─── ACCEPTED / INVALID ────▤
        │  4. 202 + status           │                             │
        ◄────────────────────────├                             │
        │                           │  5. SNS → SQS notification   │
        │                           ◄─────────────────────────────▤
        │                           │ (updates amazon_listings DB)│
        │  6. GET /listings/status   │                             │
        ├────────────────────────►│                             │
        ◄───────── ACTIVE / SUPPRESSED ──────▤
```

**Authentication Flow:** Login with Amazon (LWA) OAuth 2.0. A long-lived Refresh Token is stored in Supabase `amazon_credentials`. The backend exchanges it for short-lived Access Tokens (TTL: 3600s) cached in Redis.

**Rate Limiting:** Amazon SP-API permits 5 requests/sec per seller. A token bucket + exponential backoff strategy (max 4 retries: 1s, 2s, 4s, 8s) handles burst suppression.

**New Database Tables:**
- `amazon_credentials` — Stores LWA refresh tokens and marketplace configuration per seller.
- `amazon_listings` — Maps each Dwarika `product_id` to its Amazon `asin`, `sync_status` (`PENDING` / `SUBMITTED` / `ACTIVE` / `INVALID` / `SUPPRESSED` / `ERROR`), and `validation_issues` JSONB.

### Blinkit & JioMart One-Click Quick-Commerce Listing (Spec #24)

Store administrators can list any catalog product on both Blinkit and JioMart hyperlocal quick-commerce channels with a single click. The middleware acts as a **stateful abstraction layer** that normalizes product data, runs pre-flight compliance checks, and routes payloads through two separate ingestion pipelines.

**JioMart Integration (Fynd Konnect v3 REST API):**
- Asynchronous batch catalog ingestion via `POST /v3/catalog/product` (up to 100 products/request).
- Non-blocking `trace_id` polling via Cloud Tasks until `COMPLETED` state; field-level errors stored in `validation_issues` JSONB.
- OAuth 2.0 token management (`x-access-token` header injection; programmatic refresh cycles).
- JSON facility map for warehouse-to-JioMart location binding (`jiomartLocationId` → `UCFacilityCode`).
- Marketplace-shipped order lifecycle: order + A4 invoice fetch → manifest closure → automated CIR/RTO return sync.

**Blinkit Integration (EDI + Webhook B2B Vendor Model):**
- Semantic EAN-13/UPC catalog matching: if the product already exists on Blinkit, the seller's SKU is linked directly to the active Blinkit UPC — no new catalog entry required.
- Template compilation pipeline: if the product is new to Blinkit, the middleware compiles a Blinkit-compliant CSV/Excel template and routes it to the Category Manager.
- Keyless authentication: only the `Vendor ID` / `Receiver Code` is required; security relies on IP whitelisting and Blinkit-side webhook signature validation.
- Pincode-based routing map for B2B PO assignment (e.g., `"560067"` → `"BLR_03"`).
- MRP parity enforcement: fulfillment is automatically blocked if the physical label MRP ≠ the PO MRP.
- ASN (Advanced Shipping Note) generation and transmission to Blinkit dark store after dispatch.

**Compliance Safeguards:**
- Pre-flight validation gate: EAN-13 check-digit validation, FSSAI license check (14-digit, food/beverage), image resolution (≥ 1000×1000 px, white background), MRP ≤ selling price, JioMart MOQ ≥ 500 units, JioMart shelf life ≥ 60% remaining.

**Operational Metrics (OTIF, Fill Rate, IDM) — Manager API:**
- OTIF Rate = (on-time ∩ in-full POs / total POs) × 100. Target ≥ 95%.
- Fill Rate = (Σ delivered qty / Σ ordered qty) × 100. Target ≥ 98%.
- Inventory Discrepancy Margin = (Σ |channel_stock − physical_stock| / Σ physical_stock) × 100. Target ≤ 2%.

**New Database Tables:**
- `qc_platform_listings` — Maps each `product_id`/`variant_id` to platform listing state (`sync_status`, `trace_id`, `submission_guid`, `validation_issues` JSONB) for both Blinkit and JioMart.
- `qc_platform_credentials` — Stores Fynd access tokens (JioMart) and Blinkit Vendor ID / webhook secrets.
- `qc_warehouse_mappings` — Hyperlocal routing table: JioMart facility maps and Blinkit pincode-to-facility maps.
- `qc_purchase_orders` + `qc_po_line_items` — Blinkit B2B PO ingestion records with ASN tracking and MRP/quantity validation state.



## 7. Implementation Roadmap & Strategic Operations
The strategic system roadmap is strictly prioritized over a 38-week milestone timeline:

    ┌────────────────────────────────────────────────────────────────────────┐
    │                        38-Week Project Timeline                        │
    ├──────────────┬───────────────┬────────────────────────┬────────────────┤
    │   Phase 1    │    Phase 2    │        Phase 3         │    Phase 4     │
    │  (8 Weeks)   │  (10 Weeks)   │       (12 Weeks)       │   (8 Weeks)    │
    │  Base Infra  │  IDP & POS    │     Web/Mobile Apps    │ ONDC & WhatsApp│
    └──────────────┴───────────────┴────────────────────────┴────────────────┘
- Phase 1: Core Infrastructure & Database Foundations (Weeks 1–8): Deploy Google Cloud Run container
    architectures, Secret Manager, Artifact Registry networks, and configure initial Supabase schema sets
    with functional Row Level Security constraints.

    Milestone Target: Successful authenticated logins across web and mobile configurations utilizing Supabase JWT
    infrastructure blocks.

- Phase 2: Intelligent Intake & Physical Store Hardware Operations (Weeks 9–18): Implement automated computer
    vision preprocessing routines via OpenCV and deploy the LLM-driven Intelligent Document Processing
    pipeline alongside local ZPL printing drivers.

    Milestone Target: Fully automated stock inventory ledger updates handled instantly via scanned supplier invoice
    file uploads.

- Phase 3: Omnichannel Channels & Real-Time Concurrency Guards (Weeks 19–30): Deliver the core optimized client
    Next.js e-commerce application UI alongside native cross-platform mobile apps. Connect transactional
    endpoints to the dynamic Available-to-Promise (ATP) pessimistic database lock architecture.

        Milestone Target: Zero data state variation anomalies or overselling discrepancies captured throughout continuous peak concurrent stress testing loops.

- Phase 4: External Market Ecosystem & Automated Conversational Networks (Weeks 31–38): Roll out full Beckn ONDC
        protocol seller endpoints, deploy edge verification request signature gateways, and integrate the transactional
        WhatsApp conversational shopping engine.

        Milestone Target: Successful completion of edge transactions initialized directly over external ONDC and WhatsApp
        networks.