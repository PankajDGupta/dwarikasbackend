# Dwarikas Backend — Production Readiness Assessment

**Date:** 2026-06-21 | **All 24 feature specs complete (Spec #01–#24).**  
**Code-level fixes:** ✅ All resolved on 2026-06-21  
**Remaining gate:** Manual infrastructure provisioning (Spec #25 operator steps) → Spec #26 CI/CD Runbook

---

## Overall Status

| Layer | Status | Notes |
|---|---|---|
| Application code (all 24 specs) | ✅ Complete | — |
| Dockerfile (dependency sync) | ✅ **RESOLVED** | All pyproject.toml packages installed via uv |
| Dockerfile (health check path) | ✅ **RESOLVED** | Points to `/api/v1/health/` |
| Dockerfile (Gunicorn tuning) | ✅ **RESOLVED** | 5 workers, threads, max-requests |
| Django settings (CORS) | ✅ **RESOLVED** | Env-var controlled `CORS_ALLOWED_ORIGINS` |
| Django settings (CLOUD_RUN_SERVICE_URL) | ✅ **RESOLVED** | Added to settings.py |
| supabase/snippets/001_initial_schema.sql | ✅ **RESOLVED** | Created from system_design.md |
| .env.example | ✅ **RESOLVED** | Created with all 29 variables |
| GCP infrastructure | 🔲 **TBD — Manual** | GCP project, APIs, IAM, Artifact Registry |
| Supabase production project | 🔲 **TBD — Manual** | New project + apply all 13 SQL snippets |
| Redis (Memorystore) | 🔲 **TBD — Manual** | + VPC connector |
| Secret Manager (27 secrets) | 🔲 **TBD — Manual** | All credentials needed |
| Cloud Run Job + Service | 🔲 **TBD — Manual** | First deploy after infra ready |
| External integrations (6) | 🔲 **TBD — Operator** | Account registrations + onboarding |
| cloudbuild.yaml (CI/CD) | 🔲 **TBD — Spec #26** | Written after first manual deploy succeeds |

---

## Section 1 — ✅ RESOLVED: Code Changes (Implemented 2026-06-21)

### R-17 ✅ RESOLVED — Dockerfile: Dependency Sync

**Changed:** [`Dockerfile`](file:///h:/pankaj/projects/workspaces/dwarikas_repos/dwarikasbackend/Dockerfile) builder stage now uses:

```dockerfile
COPY pyproject.toml uv.lock ./
RUN pip install --upgrade pip setuptools wheel uv && \
    uv pip install --system -r pyproject.toml && \
    pip install gunicorn>=21.0.0
```

**Also added** to runtime stage:
```dockerfile
libgl1-mesa-glx \
libglib2.0-0 \
```
These are required by `opencv-python-headless` for Document AI image preprocessing.

---

### R-16 ✅ RESOLVED — Dockerfile: Health Check Path

**Changed:** HEALTHCHECK now points to `/api/v1/health/` (Spec #16 liveness endpoint) with `start-period=60s`:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import http.client; conn = http.client.HTTPConnection('localhost:8080'); conn.request('GET', '/api/v1/health/'); response = conn.getresponse(); exit(0 if response.status == 200 else 1)" || exit 1
```

---

### R-15 ✅ RESOLVED — Dockerfile: Gunicorn Tuning

**Changed:** CMD updated to 5 workers (optimal for 2 vCPU), with threads, max-requests (memory-leak prevention), and graceful-timeout:

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:8080",
     "--workers", "5", "--worker-class", "sync", "--threads", "2",
     "--timeout", "300", "--graceful-timeout", "30", "--keep-alive", "5",
     "--max-requests", "1000", "--max-requests-jitter", "50",
     "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info",
     "dwarikasbackend.wsgi:application"]
```

---

### R-12 ✅ RESOLVED — settings.py: CORS Restricted

**Changed:** [`settings.py`](file:///h:/pankaj/projects/workspaces/dwarikas_repos/dwarikasbackend/dwarikasbackend/settings.py) CORS block replaced with env-var-driven allowlist:

```python
_cors_origins_str = os.environ.get('CORS_ALLOWED_ORIGINS', '')
if _cors_origins_str:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_str.split(',') if o.strip()]
    CORS_ALLOW_ALL_ORIGINS = False
else:
    CORS_ALLOW_ALL_ORIGINS = True  # local dev only
```

**Production action needed:** Add `CORS_ALLOWED_ORIGINS` to Secret Manager:
```
https://app.dwarikas.com,https://admin.dwarikas.com,https://www.dwarikas.com
```

---

### R-8/4.8 ✅ RESOLVED — settings.py: CLOUD_RUN_SERVICE_URL Added

**Changed:** [`settings.py`](file:///h:/pankaj/projects/workspaces/dwarikas_repos/dwarikasbackend/dwarikasbackend/settings.py) now reads:

```python
CLOUD_RUN_SERVICE_URL = os.environ.get(
    'CLOUD_RUN_SERVICE_URL',
    'http://localhost:8080',  # local dev fallback
)
```

**Production action needed:** Set as plain env var in Cloud Run deploy command:
```bash
--set-env-vars="CLOUD_RUN_SERVICE_URL=https://dwarikas-django-api-XXXX.a.run.app"
```

---

### 001_initial_schema.sql ✅ RESOLVED — Base Schema SQL File Created

**Created:** [`supabase/snippets/001_initial_schema.sql`](file:///h:/pankaj/projects/workspaces/dwarikas_repos/dwarikasbackend/supabase/snippets/001_initial_schema.sql)

Extracted from `system_design.md`. Covers:
- `public.profiles` (RBAC roles)
- `public.products` (catalog)
- `public.product_variants` (SKU + stock)
- `public.promotions` (must precede reservations for FK)
- `public.reservations` (checkout holds)
- `public.orders` (transaction log)
- RLS policies for all tables
- `check_user_is_staff()` and `check_user_is_manager()` security definer helpers

Apply this **first** before any other numbered snippet.

---

### .env.example ✅ RESOLVED — Local Dev Template Created

**Created:** [`.env.example`](file:///h:/pankaj/projects/workspaces/dwarikas_repos/dwarikasbackend/.env.example)

All 29 environment variables documented with:
- Local dev default values
- `[SECRET MANAGER]` annotations for production secrets

---

### R-13, R-14 ✅ Already Correct (No Change Needed)

- **R-13 ALLOWED_HOSTS:** Already read from env (`os.environ.get('ALLOWED_HOSTS', '*')`). Set `ALLOWED_HOSTS=api.dwarikas.com` in Secret Manager.
- **R-14 DEBUG=False:** Already controlled by `os.environ.get('DEBUG', 'False')`. Cloud Run deploy sets `DEBUG=False` via `--set-env-vars`.

---

## Section 2 — 🔲 TBD: Manual Infrastructure Prerequisites (Operator)

> Perform these steps once before the first production Cloud Run deployment.  
> Full gcloud commands are in [`spec25-Cloud_Run_Deployment_Readiness.md`](file:///h:/pankaj/projects/workspaces/dwarikas_repos/dwarikasbackend/context/feature-spec/spec25-Cloud_Run_Deployment_Readiness.md).

### 2.1 TBD — GCP Project & APIs

```bash
# Create project
gcloud projects create dwarikas-prod --name="Dwarikas Production"

# Enable all required APIs
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  cloudtasks.googleapis.com storage.googleapis.com \
  documentai.googleapis.com redis.googleapis.com \
  vpcaccess.googleapis.com --project=$PROJECT_ID
```

### 2.2 TBD — IAM Service Accounts (2 accounts, 9 role bindings)

- Cloud Run SA: `cloudrun-serviceaccount` → secretmanager.secretAccessor, storage.objectAdmin, cloudtasks.enqueuer, run.invoker, documentai.apiUser
- Cloud Build SA: `cloudbuild-serviceaccount` → artifactregistry.writer, run.admin, secretmanager.secretAccessor, serviceAccountUser

### 2.3 TBD — Artifact Registry

```bash
gcloud artifacts repositories create dwarikas-repo \
  --repository-format=docker --location=asia-south1
```

### 2.4 TBD — Secret Manager (27 Secrets)

| # | Secret Key | Value Source |
|---|---|---|
| 1 | `DJANGO_SECRET_KEY` | Generate 50-char random string |
| 2 | `DATABASE_URL` | Supabase Dashboard → Settings → Database → Pooler URI (port 6543) |
| 3 | `SUPABASE_JWT_SECRET` | Supabase Dashboard → Settings → API → JWT Secret |
| 4 | `REDIS_URL` | After Memorystore creation: `redis://10.x.x.x:6379/0` |
| 5 | `GCS_BUCKET_NAME` | `dwarikas-prod-media` |
| 6 | `GCP_PROJECT_ID` | Your GCP project ID |
| 7 | `CLOUD_TASKS_QUEUE` | `dwarikas-task-queue` |
| 8 | `CLOUD_TASKS_REGION` | `asia-south1` |
| 9 | `DOCUMENT_AI_PROCESSOR_ID` | After creating Document AI processor |
| 10 | `EXTERNAL_SIGNING_SECRET` | Generate 32-char random string |
| 11 | `WHATSAPP_VERIFY_TOKEN` | Choose a secure random token |
| 12 | `WHATSAPP_API_TOKEN` | Meta Business Suite → WhatsApp API Setup |
| 13 | `WHATSAPP_PHONE_NUMBER_ID` | Meta App Dashboard |
| 14 | `AMAZON_LWA_CLIENT_ID` | Amazon SP-API app registration |
| 15 | `AMAZON_LWA_CLIENT_SECRET` | Amazon SP-API app registration |
| 16 | `AMAZON_SELLER_ID` | Seller Central → Account Info |
| 17 | `AMAZON_SQS_QUEUE_URL` | AWS SQS queue for listing events |
| 18 | `RAZORPAY_KEY_ID` | Razorpay Dashboard → Settings → API Keys (live) |
| 19 | `RAZORPAY_KEY_SECRET` | Razorpay Dashboard |
| 20 | `RAZORPAY_WEBHOOK_SECRET` | Razorpay → Settings → Webhooks |
| 21 | `ONDC_SUBSCRIBER_ID` | `dwarikas.com` (registered with ONDC) |
| 22 | `ONDC_PRIVATE_KEY` | Ed25519 private key (Base64) |
| 23 | `ALLOWED_HOSTS` | `api.dwarikas.com` |
| 24 | `FYND_ACCESS_TOKEN` | Fynd Konnect extension settings |
| 25 | `BLINKIT_VENDOR_ID` | Blinkit vendor onboarding |
| 26 | `BLINKIT_WEBHOOK_SECRET` | Blinkit vendor onboarding |
| 27 | `CORS_ALLOWED_ORIGINS` | `https://app.dwarikas.com,https://admin.dwarikas.com` |

### 2.5 TBD — Infrastructure Resources

| Resource | Command |
|---|---|
| GCS bucket | `gcloud storage buckets create gs://dwarikas-prod-media --location=ASIA-SOUTH1 --uniform-bucket-level-access` |
| VPC connector | `gcloud compute networks vpc-access connectors create dwarikas-vpc-connector --region=asia-south1 --range=10.8.0.0/28` |
| Redis Memorystore | `gcloud redis instances create dwarikas-redis --size=1 --region=asia-south1 --tier=BASIC` |
| Cloud Tasks queue | `gcloud tasks queues create dwarikas-task-queue --location=asia-south1 --max-dispatches-per-second=10 --max-concurrent-dispatches=5` |
| Document AI | GCP Console → Document AI → Create Processor → Invoice Parser → note Processor ID |

### 2.6 TBD — Supabase Production Project

1. Create project at app.supabase.com → region: **South Asia (Mumbai)**
2. Copy JWT Secret → Secret #3 `SUPABASE_JWT_SECRET`
3. Copy Pooler URI (port 6543) → Secret #2 `DATABASE_URL`
4. Apply SQL snippets **in order** via Supabase SQL Editor:
   ```
   001_initial_schema.sql          ← newly created
   002_product_catalog_multicategory.sql
   003_gaming_integration.sql
   003_loose_commodity_repackaging.sql
   003_payment_transactions.sql
   004_invoice_upload_and_tasks_dispatch.sql
   005_external_partner_api_gateway.sql
   006_pos_cash_sales.sql
   007_promotions_and_discounts.sql
   008_coupon_code_creation_and_application.sql
   009_smart_discount_suggestions_engine.sql
   010_amazon_sp_api_one_click_listing.sql
   011_quick_commerce_listing.sql
   ```
5. Set first manager in Auth → Users → Edit User → `app_metadata = { "role": "manager" }`

### 2.7 TBD — External Integration Onboarding

| Integration | What's Required |
|---|---|
| **Razorpay** | Register + KYC, generate live API keys, register webhook URL `https://api.dwarikas.com/api/v1/payments/razorpay/webhook/` |
| **Meta/WhatsApp** | Business Suite account, Developer App, configure webhook `https://api.dwarikas.com/api/v1/whatsapp/webhook/` |
| **Amazon SP-API** | Seller Central + LWA app, SNS/SQS pipeline for `LISTINGS_ITEM_STATUS_CHANGE` notifications |
| **ONDC** | Apply for seller node, generate Ed25519 keypair, submit public key + subscriber ID `dwarikas.com` |
| **JioMart/Fynd** | Seller Portal → Fynd Platform → Fynd Konnect extension → extract access token |
| **Blinkit** | Contact Category Manager, get Vendor ID + Receiver Code, provide webhook URL + configure IP allowlist |

---

## Section 3 — TBD: Cloud Run Deployment Commands

### Migration Job (create once)
```bash
gcloud run jobs create dwarikas-migrate-job \
  --image=asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:latest \
  --region=asia-south1 \
  --service-account=cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com \
  --command=python --args="manage.py,migrate,--no-input" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,DJANGO_SECRET_KEY=DJANGO_SECRET_KEY:latest,SUPABASE_JWT_SECRET=SUPABASE_JWT_SECRET:latest" \
  --vpc-connector=dwarikas-vpc-connector --vpc-egress=all-traffic
```

### First Cloud Run Service Deploy
```bash
gcloud run deploy dwarikas-django-api \
  --image=asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:latest \
  --region=asia-south1 --platform=managed --allow-unauthenticated \
  --service-account=cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com \
  --cpu=2 --memory=4Gi --min-instances=0 --max-instances=20 \
  --concurrency=80 --cpu-always-allocated --port=8080 --timeout=300 \
  --vpc-connector=dwarikas-vpc-connector --vpc-egress=all-traffic \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,DJANGO_SECRET_KEY=DJANGO_SECRET_KEY:latest,SUPABASE_JWT_SECRET=SUPABASE_JWT_SECRET:latest,REDIS_URL=REDIS_URL:latest,GCS_BUCKET_NAME=GCS_BUCKET_NAME:latest,GCP_PROJECT_ID=GCP_PROJECT_ID:latest,CLOUD_TASKS_QUEUE=CLOUD_TASKS_QUEUE:latest,CLOUD_TASKS_REGION=CLOUD_TASKS_REGION:latest,DOCUMENT_AI_PROCESSOR_ID=DOCUMENT_AI_PROCESSOR_ID:latest,EXTERNAL_SIGNING_SECRET=EXTERNAL_SIGNING_SECRET:latest,WHATSAPP_VERIFY_TOKEN=WHATSAPP_VERIFY_TOKEN:latest,WHATSAPP_API_TOKEN=WHATSAPP_API_TOKEN:latest,WHATSAPP_PHONE_NUMBER_ID=WHATSAPP_PHONE_NUMBER_ID:latest,AMAZON_LWA_CLIENT_ID=AMAZON_LWA_CLIENT_ID:latest,AMAZON_LWA_CLIENT_SECRET=AMAZON_LWA_CLIENT_SECRET:latest,AMAZON_SELLER_ID=AMAZON_SELLER_ID:latest,AMAZON_SQS_QUEUE_URL=AMAZON_SQS_QUEUE_URL:latest,RAZORPAY_KEY_ID=RAZORPAY_KEY_ID:latest,RAZORPAY_KEY_SECRET=RAZORPAY_KEY_SECRET:latest,RAZORPAY_WEBHOOK_SECRET=RAZORPAY_WEBHOOK_SECRET:latest,ONDC_SUBSCRIBER_ID=ONDC_SUBSCRIBER_ID:latest,ONDC_PRIVATE_KEY=ONDC_PRIVATE_KEY:latest,ALLOWED_HOSTS=ALLOWED_HOSTS:latest,FYND_ACCESS_TOKEN=FYND_ACCESS_TOKEN:latest,BLINKIT_VENDOR_ID=BLINKIT_VENDOR_ID:latest,BLINKIT_WEBHOOK_SECRET=BLINKIT_WEBHOOK_SECRET:latest,CORS_ALLOWED_ORIGINS=CORS_ALLOWED_ORIGINS:latest" \
  --set-env-vars="DEBUG=False,DJANGO_SETTINGS_MODULE=dwarikasbackend.settings,CLOUD_RUN_SERVICE_URL=https://YOUR-SERVICE-URL-HERE"
```

> After first deploy, note the service URL and update `CLOUD_RUN_SERVICE_URL` in `--set-env-vars`.

---

## Section 4 — TBD: Smoke Tests (Post-Deploy)

```bash
SERVICE_URL="https://dwarikas-django-api-XXXX.a.run.app"

# 1. Health check
curl -f $SERVICE_URL/api/v1/health/
# Expected: {"status": "ok", "database": "ok", "cache": "ok"}

# 2. Public catalog
curl -f $SERVICE_URL/api/v1/products/
# Expected: 200 paginated (empty list OK)

# 3. Auth rejection
curl -o /dev/null -s -w "%{http_code}" $SERVICE_URL/api/v1/checkout/reserve/
# Expected: 401

# 4. ONDC endpoint exists
curl -o /dev/null -s -w "%{http_code}" $SERVICE_URL/api/v1/ondc/
# Expected: 405
```

---

## Section 5 — TBD: Spec #26 — CI/CD cloudbuild.yaml

To be written after the first manual deployment succeeds. Will include:
- Automated Docker build + push to Artifact Registry
- Cloud Run Job execution for migrations (`--wait`)
- Canary/traffic-split deployment strategy
- Rollback procedure
- Cloud Monitoring alert policies
