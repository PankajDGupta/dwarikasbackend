# Spec #25 — Google Cloud Run Deployment Readiness

**Project:** Dwarika's Omnichannel Tech Ecosystem  
**Spec Type:** Pre-Deployment Readiness Checklist & Infrastructure Setup  
**Sequence:** Spec #25. Preceded by Spec #24b (Local Frontend–Backend Integration Test). The final spec (Spec #26) is the Production Deployment Runbook.  
**Status:** 🔲 Not started

---

## 1. Purpose & Scope

This spec defines every requirement — both **code-level changes** and **manual infrastructure prerequisites** — that must be satisfied before the Dwarikas Django backend can be safely deployed to Google Cloud Run (production). It is structured as a checklist: implementation engineers complete the code-level changes (tracked in `task.md`), while the operator (store owner / DevOps) follows the step-by-step manual setup sections.

Completing this spec is the gate before executing Spec #26 (Production Deployment Runbook).

---

## 2. Readiness Checklist Overview

| # | Category | Item | Type | Status |
|---|---|---|---|---|
| R-01 | GCP Project | Create & configure GCP project | Manual | 🔲 |
| R-02 | GCP Billing | Enable billing on the project | Manual | 🔲 |
| R-03 | GCP APIs | Enable required Cloud APIs | Manual | 🔲 |
| R-04 | IAM | Create Cloud Run service account & bind roles | Manual | 🔲 |
| R-05 | IAM | Create Cloud Build service account & bind roles | Manual | 🔲 |
| R-06 | Artifact Registry | Create Docker image repository | Manual | 🔲 |
| R-07 | Secret Manager | Populate all required application secrets | Manual | 🔲 |
| R-08 | Cloud Storage | Create private GCS bucket for invoices & media | Manual | 🔲 |
| R-09 | Cloud Tasks | Create task queue | Manual | 🔲 |
| R-10 | Redis (Memorystore) | Provision Redis instance for cache & rate-limiting | Manual | 🔲 |
| R-11 | Supabase | Create production Supabase project & apply schema | Manual | 🔲 |
| R-12 | CORS | Restrict CORS origins to production domains | Code | 🔲 |
| R-13 | ALLOWED_HOSTS | Set exact production domain in `ALLOWED_HOSTS` | Code | 🔲 |
| R-14 | DEBUG | Confirm `DEBUG=False` in production secret | Code/Config | 🔲 |
| R-15 | Gunicorn | Tune worker count for 2 vCPU Cloud Run instance | Code | 🔲 |
| R-16 | Health check | Point Dockerfile `HEALTHCHECK` to `/api/v1/health/` | Code | 🔲 |
| R-17 | Dockerfile | Add `opencv-python-headless` and `gunicorn` to pip install | Code | 🔲 |
| R-18 | Cloud Run Job | Create the database migration job definition | Manual | 🔲 |
| R-19 | Cloud Run Service | First-time Cloud Run service configuration | Manual | 🔲 |
| R-20 | Amazon SP-API | Register LWA app, configure SNS/SQS pipeline | Manual | 🔲 |
| R-21 | ONDC | Register as ONDC Seller Node, upload Ed25519 keys | Manual | 🔲 |
| R-22 | WhatsApp | Configure Meta webhook & verify token | Manual | 🔲 |
| R-23 | Blinkit/JioMart | Fynd onboarding & Blinkit vendor ID registration | Manual | 🔲 |
| R-24 | Razorpay | Create Razorpay account, obtain webhook secret | Manual | 🔲 |
| R-25 | Smoke tests | Run deployment smoke test suite against staging | Code/Test | 🔲 |

---

## 3. Manual Pre-Requisite Steps (Operator Guide)

> **Who performs these steps:** The store owner, DevOps engineer, or any person with Google Cloud admin access. These steps are performed **once** before the first production deployment and do not need to be repeated on subsequent deploys.

---

### 3.1 Google Cloud Project Setup

#### Step 1 — Create a GCP Project

1. Go to [https://console.cloud.google.com/](https://console.cloud.google.com/).
2. Click **Select a project** → **New Project**.
3. Set **Project name:** `dwarikas-prod` (or your preferred name).
4. Note the auto-generated **Project ID** (e.g., `dwarikas-prod-123456`). This will be referred to as `$PROJECT_ID` throughout this spec.
5. Click **Create**.

#### Step 2 — Enable Billing

1. In the GCP Console, navigate to **Billing**.
2. Link a billing account to `$PROJECT_ID`.
3. Confirm the billing status shows **Active**.

#### Step 3 — Enable Required Cloud APIs

Run the following command in Google Cloud Shell or from any machine with the `gcloud` CLI installed and authenticated:

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudtasks.googleapis.com \
  storage.googleapis.com \
  documentai.googleapis.com \
  redis.googleapis.com \
  vpcaccess.googleapis.com \
  sqladmin.googleapis.com \
  --project=$PROJECT_ID
```

> **Verify:** Each service should show `Operation finished successfully`.

---

### 3.2 IAM Service Account Setup

#### Step 4 — Create the Cloud Run Service Account

This account is the identity assumed by the running Django container.

```bash
gcloud iam service-accounts create cloudrun-serviceaccount \
  --display-name="Dwarikas Cloud Run Runtime SA" \
  --project=$PROJECT_ID
```

#### Step 5 — Grant Roles to the Cloud Run Service Account

```bash
# Read secrets at runtime
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Read and write GCS objects (invoice uploads, barcode images)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# Enqueue Cloud Tasks (invoice OCR, analytics, ONDC callbacks)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudtasks.enqueuer"

# Invoke Cloud Run task-handler endpoints (Cloud Tasks target)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# Call Document AI processor
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/documentai.apiUser"
```

#### Step 6 — Create the Cloud Build Service Account

```bash
gcloud iam service-accounts create cloudbuild-serviceaccount \
  --display-name="Dwarikas Cloud Build CI/CD SA" \
  --project=$PROJECT_ID
```

```bash
# Push images to Artifact Registry
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudbuild-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

# Deploy to Cloud Run
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudbuild-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.admin"

# Read secrets during build (if needed for migration job)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:cloudbuild-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Act as the Cloud Run SA when deploying jobs
gcloud iam service-accounts add-iam-policy-binding \
  cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com \
  --member="serviceAccount:cloudbuild-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser" \
  --project=$PROJECT_ID
```

---

### 3.3 Artifact Registry

#### Step 7 — Create the Docker Repository

```bash
gcloud artifacts repositories create dwarikas-repo \
  --repository-format=docker \
  --location=asia-south1 \
  --description="Dwarikas production container images" \
  --project=$PROJECT_ID
```

> **Verify:** Run `gcloud artifacts repositories list --project=$PROJECT_ID` and confirm `dwarikas-repo` appears.

---

### 3.4 Google Secret Manager

#### Step 8 — Create and Populate All Application Secrets

Each secret below maps to an environment variable read by `dwarikasbackend/settings.py` or app modules. Create each one using the `gcloud` command or the GCP Console UI (**Security → Secret Manager → Create Secret**).

```bash
# 1. Django master secret key — generate a new 50-char random string
echo -n "your-50-character-random-django-secret-key" | \
  gcloud secrets create DJANGO_SECRET_KEY --data-file=- --project=$PROJECT_ID

# 2. Supabase production DB connection string
# Format: postgresql://postgres.[ref]:[password]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
echo -n "postgresql://..." | \
  gcloud secrets create DATABASE_URL --data-file=- --project=$PROJECT_ID

# 3. Supabase JWT secret (from Supabase Dashboard → Project Settings → API → JWT Secret)
echo -n "your-supabase-jwt-secret" | \
  gcloud secrets create SUPABASE_JWT_SECRET --data-file=- --project=$PROJECT_ID

# 4. Redis URL (from Memorystore — see Step 12)
echo -n "redis://10.x.x.x:6379/0" | \
  gcloud secrets create REDIS_URL --data-file=- --project=$PROJECT_ID

# 5. GCS bucket name
echo -n "dwarikas-prod-media" | \
  gcloud secrets create GCS_BUCKET_NAME --data-file=- --project=$PROJECT_ID

# 6. Google Cloud project ID (used by Cloud Tasks and Document AI clients)
echo -n "$PROJECT_ID" | \
  gcloud secrets create GCP_PROJECT_ID --data-file=- --project=$PROJECT_ID

# 7. Cloud Tasks queue name
echo -n "dwarikas-task-queue" | \
  gcloud secrets create CLOUD_TASKS_QUEUE --data-file=- --project=$PROJECT_ID

# 8. Cloud Tasks queue region
echo -n "asia-south1" | \
  gcloud secrets create CLOUD_TASKS_REGION --data-file=- --project=$PROJECT_ID

# 9. Document AI processor ID (from Step 14)
echo -n "your-document-ai-processor-id" | \
  gcloud secrets create DOCUMENT_AI_PROCESSOR_ID --data-file=- --project=$PROJECT_ID

# 10. External partner signing secret (HMAC-SHA256 key for partner API gateway)
echo -n "your-external-signing-secret-32-chars" | \
  gcloud secrets create EXTERNAL_SIGNING_SECRET --data-file=- --project=$PROJECT_ID

# 11. WhatsApp verify token (for Meta webhook handshake)
echo -n "your-whatsapp-verify-token" | \
  gcloud secrets create WHATSAPP_VERIFY_TOKEN --data-file=- --project=$PROJECT_ID

# 12. WhatsApp Cloud API access token (from Meta Business Suite)
echo -n "your-whatsapp-api-token" | \
  gcloud secrets create WHATSAPP_API_TOKEN --data-file=- --project=$PROJECT_ID

# 13. WhatsApp phone number ID (from Meta App Dashboard)
echo -n "your-whatsapp-phone-number-id" | \
  gcloud secrets create WHATSAPP_PHONE_NUMBER_ID --data-file=- --project=$PROJECT_ID

# 14. Amazon LWA client ID (for SP-API OAuth)
echo -n "your-amazon-lwa-client-id" | \
  gcloud secrets create AMAZON_LWA_CLIENT_ID --data-file=- --project=$PROJECT_ID

# 15. Amazon LWA client secret
echo -n "your-amazon-lwa-client-secret" | \
  gcloud secrets create AMAZON_LWA_CLIENT_SECRET --data-file=- --project=$PROJECT_ID

# 16. Amazon seller ID
echo -n "your-amazon-seller-id" | \
  gcloud secrets create AMAZON_SELLER_ID --data-file=- --project=$PROJECT_ID

# 17. Amazon SQS queue URL (for listing status webhooks)
echo -n "https://sqs.ap-south-1.amazonaws.com/..." | \
  gcloud secrets create AMAZON_SQS_QUEUE_URL --data-file=- --project=$PROJECT_ID

# 18. Razorpay key ID
echo -n "rzp_live_xxxxx" | \
  gcloud secrets create RAZORPAY_KEY_ID --data-file=- --project=$PROJECT_ID

# 19. Razorpay key secret
echo -n "your-razorpay-secret" | \
  gcloud secrets create RAZORPAY_KEY_SECRET --data-file=- --project=$PROJECT_ID

# 20. Razorpay webhook secret
echo -n "your-razorpay-webhook-secret" | \
  gcloud secrets create RAZORPAY_WEBHOOK_SECRET --data-file=- --project=$PROJECT_ID

# 21. ONDC subscriber ID (unique URI registered with ONDC network)
echo -n "dwarikas.com" | \
  gcloud secrets create ONDC_SUBSCRIBER_ID --data-file=- --project=$PROJECT_ID

# 22. ONDC private key (Ed25519, Base64 encoded)
echo -n "your-base64-ondc-private-key" | \
  gcloud secrets create ONDC_PRIVATE_KEY --data-file=- --project=$PROJECT_ID

# 23. Production ALLOWED_HOSTS (comma-separated)
echo -n "api.dwarikas.com,dwarikas.com" | \
  gcloud secrets create ALLOWED_HOSTS --data-file=- --project=$PROJECT_ID

# 24. Fynd/JioMart access token
echo -n "your-fynd-access-token" | \
  gcloud secrets create FYND_ACCESS_TOKEN --data-file=- --project=$PROJECT_ID

# 25. Blinkit vendor ID
echo -n "BLK-VND-XXXXX" | \
  gcloud secrets create BLINKIT_VENDOR_ID --data-file=- --project=$PROJECT_ID

# 26. Blinkit webhook secret
echo -n "your-blinkit-webhook-secret" | \
  gcloud secrets create BLINKIT_WEBHOOK_SECRET --data-file=- --project=$PROJECT_ID
```

> **Security rule:** Never put these values in source code, `.env` files committed to git, or Cloud Run environment variable plain text. All secrets must go through Secret Manager.

---

### 3.5 Google Cloud Storage (GCS)

#### Step 9 — Create the Production Media Bucket

```bash
gcloud storage buckets create gs://dwarikas-prod-media \
  --project=$PROJECT_ID \
  --location=ASIA-SOUTH1 \
  --uniform-bucket-level-access \
  --no-public-access-prevention
```

> **Important:** The bucket must be **private** (uniform bucket-level access, no public access). Signed URLs are generated by the Django backend for time-limited file access (e.g., HITL invoice review).

Grant the Cloud Run service account full object access:

```bash
gcloud storage buckets add-iam-policy-binding gs://dwarikas-prod-media \
  --member="serviceAccount:cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

---

### 3.6 Google Cloud Tasks

#### Step 10 — Create the Task Queue

```bash
gcloud tasks queues create dwarikas-task-queue \
  --location=asia-south1 \
  --max-dispatches-per-second=10 \
  --max-concurrent-dispatches=5 \
  --project=$PROJECT_ID
```

> **Note:** The queue is used by:
> - Document AI OCR invoice processing (Spec #10 / #11)
> - ONDC asynchronous callback routing (Spec #13)
> - Smart discount suggestions nightly analytics (Spec #22)
> - JioMart `trace_id` polling (Spec #24)

---

### 3.7 Redis (Cloud Memorystore)

#### Step 11 — Provision a Redis Instance

Redis is required for:
- Django rate throttling (Spec #16: `django-axes`, DRF throttle)
- JTI blocklist (session revocation, Spec #16)
- Amazon SP-API access token caching (Spec #23)

```bash
# Create a Serverless VPC Access connector first (needed for Memorystore)
gcloud compute networks vpc-access connectors create dwarikas-vpc-connector \
  --region=asia-south1 \
  --range=10.8.0.0/28 \
  --project=$PROJECT_ID

# Create the Redis instance (Basic tier, 1 GB)
gcloud redis instances create dwarikas-redis \
  --size=1 \
  --region=asia-south1 \
  --tier=BASIC \
  --project=$PROJECT_ID
```

After creation, note the Redis **host IP**:
```bash
gcloud redis instances describe dwarikas-redis --region=asia-south1 --project=$PROJECT_ID \
  | grep host
```

Update the `REDIS_URL` secret (created in Step 8) with the actual IP:
```bash
echo -n "redis://10.x.x.x:6379/0" | \
  gcloud secrets versions add REDIS_URL --data-file=- --project=$PROJECT_ID
```

> **Important:** Cloud Run must be connected to the VPC connector (`dwarikas-vpc-connector`) to reach Memorystore. This is configured in Step 19 (Cloud Run service creation).

---

### 3.8 Google Document AI

#### Step 12 — Create a Document AI Processor

Document AI is used for OCR invoice parsing (Spec #11).

1. Go to **Document AI** in the GCP Console.
2. Click **Create Processor** → select **Invoice Parser** (or **General Document Parser** if Invoice Parser is not available in `asia-south1`).
3. Name it `dwarikas-invoice-parser`.
4. Select region `asia-south1` (or `us` if `asia-south1` is not supported — update `CLOUD_TASKS_REGION` accordingly).
5. After creation, note the **Processor ID** (e.g., `abc1234567890`).
6. Update the `DOCUMENT_AI_PROCESSOR_ID` secret (Step 8) with this value.

---

### 3.9 Supabase Production Database

#### Step 13 — Create and Configure Production Supabase Project

1. Go to [https://app.supabase.com/](https://app.supabase.com/) → **New Project**.
2. Choose region: **South Asia (Mumbai)** for lowest latency to GCP `asia-south1`.
3. Note the **Project Reference ID** (visible in the project URL: `https://app.supabase.com/project/<ref>`).
4. Navigate to **Project Settings → API** and copy the **JWT Secret** — this goes into `SUPABASE_JWT_SECRET` (Step 8).
5. Navigate to **Project Settings → Database** → copy the **Connection String** (URI format, use the **connection pooler URI** on port `6543` for production). This goes into `DATABASE_URL` (Step 8).

#### Step 14 — Apply the Production Database Schema

Apply each SQL snippet from `supabase/snippets/` **in order** using the Supabase SQL editor (`https://app.supabase.com/project/<ref>/sql`):

```
001_initial_schema.sql
002_product_catalog_multicategory.sql
... (any subsequent snippets)
```

> **Rule:** Apply each file's statements one block at a time in the Supabase SQL editor. Do not batch multiple files together.

#### Step 15 — Set Supabase App Metadata Role for Admin User

For the first manager account, set their role in Supabase Auth:

1. Go to **Authentication → Users** in Supabase Dashboard.
2. Find the admin user → click **Edit User**.
3. Set **Custom Claims** (`app_metadata`):
   ```json
   { "role": "manager" }
   ```
4. Save. All subsequent staff/manager roles should be set the same way.

---

### 3.10 External Integration Prerequisites

#### Step 16 — Razorpay Account Setup (Spec #17)

1. Register at [https://razorpay.com/](https://razorpay.com/) and complete KYC.
2. Go to **Settings → API Keys** → generate a **Live** key pair.
3. Store `rzp_live_*` key ID in `RAZORPAY_KEY_ID` secret and key secret in `RAZORPAY_KEY_SECRET` secret.
4. Go to **Settings → Webhooks** → add a new webhook:
   - URL: `https://api.dwarikas.com/api/v1/payments/razorpay/webhook/`
   - Events: `payment.captured`, `payment.failed`, `order.paid`
   - Copy the **Webhook Secret** → store in `RAZORPAY_WEBHOOK_SECRET` secret.

#### Step 17 — Meta WhatsApp Business API Setup (Spec #14)

1. Create a **Meta Business Suite** account at [https://business.facebook.com/](https://business.facebook.com/).
2. Add a **WhatsApp Business** account and register the store phone number.
3. Create a **Meta Developer App** → add the **WhatsApp** product.
4. Go to **WhatsApp → API Setup** → copy the **Temporary / Permanent Access Token** → store in `WHATSAPP_API_TOKEN` secret.
5. Copy the **Phone Number ID** → store in `WHATSAPP_PHONE_NUMBER_ID` secret.
6. Go to **WhatsApp → Configuration → Webhook**:
   - Callback URL: `https://api.dwarikas.com/api/v1/whatsapp/webhook/`
   - Verify Token: (any secure random string you choose) → store this same value in `WHATSAPP_VERIFY_TOKEN` secret.
   - Subscribe to fields: `messages`.

#### Step 18 — Amazon SP-API Setup (Spec #23)

1. Register as an Amazon seller on Amazon Seller Central ([https://sellercentral.amazon.in/](https://sellercentral.amazon.in/)).
2. Go to **Apps & Services → Develop Apps** → **Create new app**.
3. Note the **LWA Client ID** and **LWA Client Secret** → store in `AMAZON_LWA_CLIENT_ID` and `AMAZON_LWA_CLIENT_SECRET` secrets.
4. Authorize the app from Seller Central to obtain the **Refresh Token** → store in a `AMAZON_LWA_REFRESH_TOKEN` secret.
5. Note your **Seller ID** (visible in Seller Central → Account Info) → store in `AMAZON_SELLER_ID` secret.
6. Set up the SNS/SQS webhook pipeline for listing status notifications:
   - In AWS Console, create an SNS topic `dwarikas-amazon-listing-events` (region: `ap-south-1`).
   - Create an SQS queue `dwarikas-amazon-listing-queue` and subscribe it to the SNS topic.
   - Register the SNS subscription URL with Amazon SP-API Notifications API (`POST /notifications/v1/subscriptions/LISTINGS_ITEM_STATUS_CHANGE`).
   - Store the SQS queue URL in `AMAZON_SQS_QUEUE_URL` secret.

#### Step 19 — ONDC Seller Node Registration (Spec #13)

1. Apply for ONDC seller node registration at [https://ondc.org/](https://ondc.org/).
2. Generate an **Ed25519** key pair:
   ```bash
   openssl genpkey -algorithm ed25519 -out ondc_private.pem
   openssl pkey -in ondc_private.pem -pubout -out ondc_public.pem
   ```
3. Submit the **public key** and your **subscriber ID** (e.g., `dwarikas.com`) during ONDC network registration.
4. Base64-encode the private key and store it in `ONDC_PRIVATE_KEY` secret:
   ```bash
   base64 -w0 ondc_private.pem | gcloud secrets create ONDC_PRIVATE_KEY --data-file=- --project=$PROJECT_ID
   ```
5. Store the subscriber ID in `ONDC_SUBSCRIBER_ID` secret.
6. Delete the local key files after storing them securely.

#### Step 20 — JioMart / Fynd Konnect Onboarding (Spec #24)

Follow the manual onboarding steps documented in **Spec #24 Section 5.1.1**:

1. Register on JioMart Seller Portal → then Fynd Platform.
2. Install the Fynd Konnect connector extension from the Fynd Extensions Marketplace.
3. Configure selling locations and warehouse codes.
4. Extract the **Fynd Username** and **Token** from extension settings.
5. Store the Fynd token in `FYND_ACCESS_TOKEN` secret.
6. Request whitelisting on the JioMart integration gateway.

#### Step 21 — Blinkit Vendor Registration (Spec #24)

Follow the onboarding steps documented in **Spec #24 Section 5.2**:

1. Contact Blinkit's Category Manager to initiate vendor onboarding.
2. Obtain your **Vendor ID** and **Receiver Code**.
3. Store the Vendor ID in `BLINKIT_VENDOR_ID` secret.
4. After vendor approval, provide Blinkit engineering with the webhook URL:
   ```
   https://api.dwarikas.com/api/v1/quickcommerce/blinkit/webhook/po/
   ```
5. Obtain and store the **webhook secret** in `BLINKIT_WEBHOOK_SECRET` secret.
6. Configure IP whitelisting with Blinkit for the Cloud Run service's egress NAT IP.

---

### 3.11 Cloud Run Infrastructure (First-Time)

#### Step 22 — Create the Database Migration Cloud Run Job

This job runs `python manage.py migrate` before each new version is deployed. It must be created once:

```bash
gcloud run jobs create dwarikas-migrate-job \
  --image=asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:latest \
  --region=asia-south1 \
  --service-account=cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com \
  --command=python \
  --args="manage.py,migrate,--no-input" \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,DJANGO_SECRET_KEY=DJANGO_SECRET_KEY:latest,SUPABASE_JWT_SECRET=SUPABASE_JWT_SECRET:latest" \
  --vpc-connector=dwarikas-vpc-connector \
  --vpc-egress=all-traffic \
  --project=$PROJECT_ID
```

#### Step 23 — Create the Cloud Run Service (First Deploy)

```bash
gcloud run deploy dwarikas-django-api \
  --image=asia-south1-docker.pkg.dev/$PROJECT_ID/dwarikas-repo/django-api:latest \
  --region=asia-south1 \
  --platform=managed \
  --allow-unauthenticated \
  --service-account=cloudrun-serviceaccount@$PROJECT_ID.iam.gserviceaccount.com \
  --cpu=2 \
  --memory=4Gi \
  --min-instances=0 \
  --max-instances=20 \
  --concurrency=80 \
  --cpu-always-allocated \
  --port=8080 \
  --timeout=300 \
  --vpc-connector=dwarikas-vpc-connector \
  --vpc-egress=all-traffic \
  --set-secrets="\
    DATABASE_URL=DATABASE_URL:latest,\
    DJANGO_SECRET_KEY=DJANGO_SECRET_KEY:latest,\
    SUPABASE_JWT_SECRET=SUPABASE_JWT_SECRET:latest,\
    REDIS_URL=REDIS_URL:latest,\
    GCS_BUCKET_NAME=GCS_BUCKET_NAME:latest,\
    GCP_PROJECT_ID=GCP_PROJECT_ID:latest,\
    CLOUD_TASKS_QUEUE=CLOUD_TASKS_QUEUE:latest,\
    CLOUD_TASKS_REGION=CLOUD_TASKS_REGION:latest,\
    DOCUMENT_AI_PROCESSOR_ID=DOCUMENT_AI_PROCESSOR_ID:latest,\
    EXTERNAL_SIGNING_SECRET=EXTERNAL_SIGNING_SECRET:latest,\
    WHATSAPP_VERIFY_TOKEN=WHATSAPP_VERIFY_TOKEN:latest,\
    WHATSAPP_API_TOKEN=WHATSAPP_API_TOKEN:latest,\
    WHATSAPP_PHONE_NUMBER_ID=WHATSAPP_PHONE_NUMBER_ID:latest,\
    AMAZON_LWA_CLIENT_ID=AMAZON_LWA_CLIENT_ID:latest,\
    AMAZON_LWA_CLIENT_SECRET=AMAZON_LWA_CLIENT_SECRET:latest,\
    AMAZON_SELLER_ID=AMAZON_SELLER_ID:latest,\
    AMAZON_SQS_QUEUE_URL=AMAZON_SQS_QUEUE_URL:latest,\
    RAZORPAY_KEY_ID=RAZORPAY_KEY_ID:latest,\
    RAZORPAY_KEY_SECRET=RAZORPAY_KEY_SECRET:latest,\
    RAZORPAY_WEBHOOK_SECRET=RAZORPAY_WEBHOOK_SECRET:latest,\
    ONDC_SUBSCRIBER_ID=ONDC_SUBSCRIBER_ID:latest,\
    ONDC_PRIVATE_KEY=ONDC_PRIVATE_KEY:latest,\
    ALLOWED_HOSTS=ALLOWED_HOSTS:latest,\
    FYND_ACCESS_TOKEN=FYND_ACCESS_TOKEN:latest,\
    BLINKIT_VENDOR_ID=BLINKIT_VENDOR_ID:latest,\
    BLINKIT_WEBHOOK_SECRET=BLINKIT_WEBHOOK_SECRET:latest" \
  --set-env-vars="DEBUG=False,DJANGO_SETTINGS_MODULE=dwarikasbackend.settings" \
  --project=$PROJECT_ID
```

> **After creation**, note the service URL (e.g., `https://dwarikas-django-api-xyz-el.a.run.app`).  
> Map this to your custom domain `api.dwarikas.com` using **Cloud Run → Domain Mappings** in the console.

---

## 4. Code-Level Changes Required

These changes must be implemented in the Django application code before the container image is built for production.

---

### 4.1 Fix CORS to Restrict Production Origins (R-12)

**File:** `dwarikasbackend/settings.py`

The current setting `CORS_ALLOW_ALL_ORIGINS = True` must be replaced with an explicit allowlist sourced from the environment variable:

```python
# Replace:
CORS_ALLOW_ALL_ORIGINS = True

# With:
CORS_ALLOWED_ORIGINS_STR = os.environ.get('CORS_ALLOWED_ORIGINS', '')
if CORS_ALLOWED_ORIGINS_STR:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in CORS_ALLOWED_ORIGINS_STR.split(',')]
    CORS_ALLOW_ALL_ORIGINS = False
else:
    # Fallback for local development only
    CORS_ALLOW_ALL_ORIGINS = True
```

Add `CORS_ALLOWED_ORIGINS` to Secret Manager (Step 8) with value:
```
https://app.dwarikas.com,https://admin.dwarikas.com,https://www.dwarikas.com
```

---

### 4.2 Set ALLOWED_HOSTS from Environment (R-13)

The `ALLOWED_HOSTS` setting already reads from the environment (`os.environ.get('ALLOWED_HOSTS', '*')`). Ensure the `ALLOWED_HOSTS` secret is populated with the exact production domain:
```
api.dwarikas.com
```

The wildcard `'*'` fallback is acceptable for local development but must never be the active value in production.

---

### 4.3 Confirm DEBUG=False in Production (R-14)

`DEBUG` is already controlled by the `DEBUG` environment variable in `settings.py`. In the Cloud Run deployment command (Step 23), `DEBUG=False` is set explicitly as an environment variable. No code change is needed — but verify the Secret Manager does **not** have a `DEBUG=True` entry that would override this.

---

### 4.4 Tune Gunicorn Workers for 2 vCPU (R-15)

**File:** `Dockerfile`

The current Dockerfile starts Gunicorn with `--workers 4`. For a 2 vCPU Cloud Run instance, the recommended worker count is `(2 × CPU cores) + 1 = 5`. Update the CMD:

```dockerfile
# Replace:
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", ...]

# With:
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "5", \
     "--worker-class", "sync", \
     "--threads", "2", \
     "--timeout", "300", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "50", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "dwarikasbackend.wsgi:application"]
```

> `--max-requests 1000` and `--max-requests-jitter 50` recycle workers periodically to prevent memory leaks in long-running Cloud Run instances.

---

### 4.5 Fix Health Check Endpoint in Dockerfile (R-16)

**File:** `Dockerfile`

The current `HEALTHCHECK` command uses the root path (`/`) which returns a 404. Point it to the actual health endpoint implemented in Spec #16:

```dockerfile
# Replace:
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import http.client; conn = http.client.HTTPConnection('localhost:8080'); conn.request('GET', '/'); response = conn.getresponse(); exit(0 if response.status == 200 else 1)" || exit 1

# With:
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import http.client; conn = http.client.HTTPConnection('localhost:8080'); conn.request('GET', '/api/v1/health/'); response = conn.getresponse(); exit(0 if response.status == 200 else 1)" || exit 1
```

> `--start-period=60s` gives Gunicorn and the database connection pool enough time to initialize before health checks begin.

---

### 4.6 Sync Dockerfile Dependencies with pyproject.toml (R-17)

**File:** `Dockerfile`

The `pyproject.toml` includes packages that are **not** in the Dockerfile's `pip install` command:

| Package | In pyproject.toml | In Dockerfile |
|---|---|---|
| `opencv-python-headless` | ✅ | ❌ |
| `numpy` | ✅ | ❌ |
| `dj-database-url` | ✅ | ❌ |
| `django-filter` | ✅ | ❌ |
| `python-barcode[images]` | ✅ | ❌ |
| `Pillow` | ✅ | ❌ |
| `django-axes` | ✅ | ❌ |
| `django-redis` | ✅ | ❌ |
| `redis` | ✅ | ❌ |
| `gunicorn` | ❌ | ✅ |

**Action:** Replace the manual `pip install` in the Dockerfile with a `pip install -e .` driven by `pyproject.toml` to keep dependencies in sync automatically:

```dockerfile
# In the builder stage, after COPY pyproject.toml ./:
COPY pyproject.toml uv.lock ./

# Install all dependencies from pyproject.toml + gunicorn
RUN pip install --upgrade pip \
    && pip install uv \
    && uv pip install --system -r pyproject.toml \
    && pip install gunicorn>=21.0.0
```

Additionally, add the OpenCV runtime libraries to the runtime stage `apt-get install`:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libssl3 \
    libffi8 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
```

> `libgl1-mesa-glx` and `libglib2.0-0` are required by `opencv-python-headless` at runtime for image preprocessing in Spec #11.

---

### 4.7 Add CORS_ALLOWED_ORIGINS to Required Secrets List

Update `settings.py` to read the new `CORS_ALLOWED_ORIGINS` environment variable as described in section 4.1.

---

### 4.8 Verify Cloud Run Task Handler URL in Settings

**File:** Any module that constructs Cloud Tasks target URLs (e.g., `inventory/tasks_service.py`)

Ensure the Cloud Tasks handler URL is constructed using the **production Cloud Run service URL** when running in production, not the local dev thread URL. Add the following environment variable:

```python
CLOUD_RUN_SERVICE_URL = os.environ.get(
    'CLOUD_RUN_SERVICE_URL',
    'http://localhost:8080'  # local dev fallback
)
```

Add `CLOUD_RUN_SERVICE_URL` as a Cloud Run environment variable in Step 23 (not a secret — it's the service's own URL):
```bash
--set-env-vars="...,CLOUD_RUN_SERVICE_URL=https://dwarikas-django-api-xyz-el.a.run.app"
```

---

## 5. Deployment Architecture Validation Checklist

Before proceeding to Spec #26, validate the following end-to-end:

### 5.1 Infrastructure Checks

| Check | Command / Action | Expected Result |
|---|---|---|
| GCP APIs enabled | `gcloud services list --project=$PROJECT_ID` | All 10 required APIs show `ENABLED` |
| Service account exists | `gcloud iam service-accounts list --project=$PROJECT_ID` | Both SAs listed |
| Artifact Registry created | `gcloud artifacts repositories list --project=$PROJECT_ID` | `dwarikas-repo` shown |
| All secrets populated | `gcloud secrets list --project=$PROJECT_ID` | All 26 secrets listed |
| GCS bucket exists | `gcloud storage buckets list --project=$PROJECT_ID` | `dwarikas-prod-media` shown |
| Cloud Tasks queue exists | `gcloud tasks queues describe dwarikas-task-queue --location=asia-south1` | Queue config returned |
| Redis instance running | `gcloud redis instances describe dwarikas-redis --region=asia-south1` | `state: READY` |
| VPC connector created | `gcloud compute networks vpc-access connectors list --region=asia-south1` | `dwarikas-vpc-connector` READY |

### 5.2 Application Configuration Checks

| Check | Method | Expected Result |
|---|---|---|
| `DEBUG=False` | Check Cloud Run env vars | `DEBUG` env var = `False` |
| `ALLOWED_HOSTS` is not `*` | Check ALLOWED_HOSTS secret | Exact domain(s) only |
| `CORS_ALLOW_ALL_ORIGINS=False` | Code review `settings.py` | Explicit origins list used |
| Gunicorn workers = 5 | Review Dockerfile CMD | `--workers 5` present |
| Health check path = `/api/v1/health/` | Review Dockerfile HEALTHCHECK | Correct path used |
| All pyproject.toml deps in Dockerfile | Compare both files | No missing packages |

### 5.3 Database Checks

| Check | Method | Expected Result |
|---|---|---|
| Production Supabase project created | Supabase Dashboard | Project shows `Active` |
| Schema snippets applied | SQL Editor in Supabase | All tables exist in `public` schema |
| Admin user role set to `manager` | Auth → Users in Supabase | `app_metadata.role = "manager"` |
| DB connection string correct | Run `gcloud secrets versions access latest --secret=DATABASE_URL` | Valid PostgreSQL URI |

### 5.4 Smoke Test Endpoints

After completing Steps 22 and 23 (first Cloud Run deploy), run these smoke tests against the live service URL:

```bash
SERVICE_URL="https://dwarikas-django-api-xyz-el.a.run.app"

# 1. Health check
curl -f $SERVICE_URL/api/v1/health/
# Expected: {"status": "ok", "database": "ok", "cache": "ok"}

# 2. Public catalog list (no auth required)
curl -f $SERVICE_URL/api/v1/products/
# Expected: 200 with paginated results (empty list is OK)

# 3. Auth rejection (no token)
curl -o /dev/null -s -w "%{http_code}" $SERVICE_URL/api/v1/checkout/reserve/
# Expected: 401

# 4. ONDC health
curl -o /dev/null -s -w "%{http_code}" $SERVICE_URL/api/v1/ondc/
# Expected: 405 (method not allowed, endpoint exists)
```

---

## 6. Environment Variable Master Reference

The following table is the single source of truth for all runtime environment variables required by the production deployment.

| Variable Name | Source | Secret Manager Key | Required | Description |
|---|---|---|---|---|
| `DJANGO_SECRET_KEY` | Secret Manager | `DJANGO_SECRET_KEY` | ✅ | Django cryptographic signing key |
| `DEBUG` | Cloud Run env var | — | ✅ | Must be `False` in production |
| `ALLOWED_HOSTS` | Secret Manager | `ALLOWED_HOSTS` | ✅ | Comma-separated production domains |
| `DATABASE_URL` | Secret Manager | `DATABASE_URL` | ✅ | Supabase PostgreSQL connection URI |
| `SUPABASE_JWT_SECRET` | Secret Manager | `SUPABASE_JWT_SECRET` | ✅ | JWT HS256 signing secret |
| `REDIS_URL` | Secret Manager | `REDIS_URL` | ✅ | Redis for cache, rate-limit, JTI blocklist |
| `GCS_BUCKET_NAME` | Secret Manager | `GCS_BUCKET_NAME` | ✅ | Invoice & media storage bucket name |
| `GCP_PROJECT_ID` | Secret Manager | `GCP_PROJECT_ID` | ✅ | Used by Cloud Tasks and Document AI clients |
| `CLOUD_TASKS_QUEUE` | Secret Manager | `CLOUD_TASKS_QUEUE` | ✅ | Task queue name |
| `CLOUD_TASKS_REGION` | Secret Manager | `CLOUD_TASKS_REGION` | ✅ | Task queue region |
| `CLOUD_RUN_SERVICE_URL` | Cloud Run env var | — | ✅ | Cloud Run's own URL (used to self-invoke task handlers) |
| `DOCUMENT_AI_PROCESSOR_ID` | Secret Manager | `DOCUMENT_AI_PROCESSOR_ID` | ✅ | Invoice OCR processor ID |
| `EXTERNAL_SIGNING_SECRET` | Secret Manager | `EXTERNAL_SIGNING_SECRET` | ✅ | HMAC signing key for partner API gateway |
| `WHATSAPP_VERIFY_TOKEN` | Secret Manager | `WHATSAPP_VERIFY_TOKEN` | ✅ | Meta webhook challenge verify token |
| `WHATSAPP_API_TOKEN` | Secret Manager | `WHATSAPP_API_TOKEN` | ✅ | Meta Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Secret Manager | `WHATSAPP_PHONE_NUMBER_ID` | ✅ | WhatsApp phone number ID |
| `AMAZON_LWA_CLIENT_ID` | Secret Manager | `AMAZON_LWA_CLIENT_ID` | ✅ | Amazon SP-API LWA client ID |
| `AMAZON_LWA_CLIENT_SECRET` | Secret Manager | `AMAZON_LWA_CLIENT_SECRET` | ✅ | Amazon SP-API LWA client secret |
| `AMAZON_SELLER_ID` | Secret Manager | `AMAZON_SELLER_ID` | ✅ | Amazon seller account ID |
| `AMAZON_SQS_QUEUE_URL` | Secret Manager | `AMAZON_SQS_QUEUE_URL` | ✅ | SQS URL for listing status webhooks |
| `RAZORPAY_KEY_ID` | Secret Manager | `RAZORPAY_KEY_ID` | ✅ | Razorpay live API key ID |
| `RAZORPAY_KEY_SECRET` | Secret Manager | `RAZORPAY_KEY_SECRET` | ✅ | Razorpay live API secret |
| `RAZORPAY_WEBHOOK_SECRET` | Secret Manager | `RAZORPAY_WEBHOOK_SECRET` | ✅ | Razorpay webhook HMAC secret |
| `ONDC_SUBSCRIBER_ID` | Secret Manager | `ONDC_SUBSCRIBER_ID` | ✅ | ONDC network subscriber ID |
| `ONDC_PRIVATE_KEY` | Secret Manager | `ONDC_PRIVATE_KEY` | ✅ | Ed25519 private key (Base64) |
| `CORS_ALLOWED_ORIGINS` | Secret Manager | `CORS_ALLOWED_ORIGINS` | ✅ | Comma-separated allowed frontend origins |
| `FYND_ACCESS_TOKEN` | Secret Manager | `FYND_ACCESS_TOKEN` | ✅ | Fynd Konnect OAuth access token (JioMart) |
| `BLINKIT_VENDOR_ID` | Secret Manager | `BLINKIT_VENDOR_ID` | ✅ | Blinkit vendor ID |
| `BLINKIT_WEBHOOK_SECRET` | Secret Manager | `BLINKIT_WEBHOOK_SECRET` | ✅ | Blinkit webhook signature secret |
| `DJANGO_SETTINGS_MODULE` | Cloud Run env var | — | ✅ | Must be `dwarikasbackend.settings` |

---

## 7. Acceptance Criteria

This spec is complete when **all of the following are true**:

- [ ] All 26 secrets are created in Secret Manager and have at least one active version.
- [ ] The GCS bucket `dwarikas-prod-media` is created and private.
- [ ] The Cloud Tasks queue `dwarikas-task-queue` is operational.
- [ ] The Redis Memorystore instance is in `READY` state.
- [ ] The VPC Access Connector is `READY` and linked to Cloud Run.
- [ ] The Document AI Invoice Parser processor is created and its ID is stored in Secret Manager.
- [ ] The Supabase production project has all schema snippets applied.
- [ ] The Cloud Run migration job `dwarikas-migrate-job` is created.
- [ ] The Cloud Run service `dwarikas-django-api` is deployed and serving.
- [ ] `GET /api/v1/health/` returns HTTP 200 with `"database": "ok"` and `"cache": "ok"`.
- [ ] `CORS_ALLOW_ALL_ORIGINS = True` has been removed from production settings.
- [ ] The Dockerfile `HEALTHCHECK` points to `/api/v1/health/`.
- [ ] The Dockerfile installs all packages from `pyproject.toml` (no missing packages).
- [ ] Gunicorn is configured with `--workers 5` for the 2 vCPU instance.
- [ ] All 4 smoke test endpoints return expected HTTP response codes.
- [ ] Razorpay, WhatsApp, Amazon SP-API, ONDC, JioMart, and Blinkit integrations are onboarded (even if not yet live — credentials must be in place).

---

## 8. Next Step

Once all acceptance criteria above are met, proceed to **Spec #26 — Production Deployment Runbook**, which defines the automated CI/CD pipeline (`cloudbuild.yaml`), rollback procedures, and ongoing operational monitoring configuration.
