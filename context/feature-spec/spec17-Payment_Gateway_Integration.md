# Spec 17 — Payment Gateway Integration (Razorpay)

## Goal

Close the security gap introduced in Spec 08 where `POST /api/v1/orders/confirm/` blindly trusted the client's claim of payment completion. Integrate Razorpay as the primary payment aggregator to:

1. **Create a Razorpay Order** server-side before the frontend renders the payment widget — this ties the amount, currency, and reservation to an immutable server-generated order ID.
2. **Verify payment signatures** server-side after the user completes payment — the frontend submits `razorpay_payment_id`, `razorpay_order_id`, and `razorpay_signature`; Django validates the HMAC before committing stock.
3. **Handle Razorpay webhooks** as the authoritative payment confirmation path — required for UPI, net banking, and auto-debit flows where the redirect may not reach the frontend.
4. **Support refunds** via a staff-only endpoint that calls the Razorpay Refunds API atomically with a stock reversal.

> **Why backend, not frontend?**
> The Razorpay secret key must never be exposed to the browser. Payment signature verification (`HMAC-SHA256` of `razorpay_order_id|razorpay_payment_id`) requires the secret and must happen server-side. Stock decrement must be atomic with payment confirmation — the backend is the only safe place to enforce this invariant.

---

## Pre-Requisites (Manual — Must Complete Before Implementation)

> [!IMPORTANT]
> All items in this section require **human action on external portals**. No code can be tested end-to-end until every step here is complete. Steps 1–4 require Razorpay KYC approval, which can take **2–5 business days**.

### Step 1 — Create a Razorpay Merchant Account

1. Go to [dashboard.razorpay.com](https://dashboard.razorpay.com) and register with your business email.
2. Verify your email address and complete the basic account setup.
3. The account starts in **Test Mode** by default. All development work uses Test Mode until KYC is approved.

### Step 2 — Complete KYC (Required for Live Payments)

Navigate to **Dashboard → Account & Settings → KYC** and upload:

| Document | Format | Notes |
|---|---|---|
| **GST Certificate** | PDF/Image | Must be active and match business name |
| **PAN Card** | PDF/Image | Business entity PAN (not personal, if Pvt Ltd) |
| **Cancelled Cheque** | PDF/Image | Bank account where Razorpay will settle funds |
| **Director's Aadhaar** | PDF/Image | Aadhaar of the authorised signatory |

> [!CAUTION]
> KYC approval takes **2–5 business days**. Until approved, only Test Mode keys work. Plan accordingly — do not schedule a production launch without confirmed KYC approval.

### Step 3 — Link a Settlement Bank Account

Navigate to **Dashboard → Account & Settings → Bank Account** and add the Dwarikas business bank account. Settlement will be credited here after Razorpay's T+2 settlement cycle.

### Step 4 — Generate API Keys

Navigate to **Dashboard → Settings → API Keys → Generate Key**:

| Key Type | When to Generate | Usage |
|---|---|---|
| **Test Mode keys** | Immediately after account creation | Local development and CI |
| **Live Mode keys** | After KYC approval only | Production Cloud Run deployment |

Two values are generated per environment:
- `RAZORPAY_KEY_ID` — starts with `rzp_test_` (test) or `rzp_live_` (live). **Safe to send to frontend.**
- `RAZORPAY_KEY_SECRET` — shown once at generation time. **Must never leave the server.**

> [!CAUTION]
> The `RAZORPAY_KEY_SECRET` is displayed **only once** at generation time. Copy it immediately and store it in a password manager before closing the dialog. If lost, you must regenerate the key pair.

### Step 5 — Register the Webhook Endpoint

Navigate to **Dashboard → Settings → Webhooks → + Add New Webhook**:

| Field | Value |
|---|---|
| **Webhook URL** | `https://<your-cloud-run-domain>/api/v1/payments/webhook/` |
| **Secret** | Generate a strong random string (store it immediately — shown once) |
| **Active Events** | ✅ `payment.captured` ✅ `payment.failed` ✅ `refund.created` |

> [!CAUTION]
> The **Webhook Secret** is a separate credential from the API Key Secret. It is shown **only once** at webhook creation time. Store it immediately. If lost, delete the webhook and recreate it.

For **local development**, Razorpay cannot reach `localhost`. Use one of:
- **ngrok**: `ngrok http 8000` — paste the `https://xxxx.ngrok-free.app` URL temporarily into the webhook settings.
- **Razorpay Test Webhook Simulator**: Dashboard → Webhooks → your webhook → Trigger Test Webhook (does not require a public URL).

### Step 6 — Store Credentials in Google Secret Manager

All three Razorpay credentials must be stored in Google Secret Manager before Cloud Run deployment. Run these commands once:

```bash
# Create the secret slots
gcloud secrets create RAZORPAY_KEY_ID --replication-policy="automatic"
gcloud secrets create RAZORPAY_KEY_SECRET --replication-policy="automatic"
gcloud secrets create RAZORPAY_WEBHOOK_SECRET --replication-policy="automatic"

# Populate with actual values from Razorpay Dashboard
echo -n "<actual_key_id>" | gcloud secrets versions add RAZORPAY_KEY_ID --data-file=-
echo -n "<actual_key_secret>" | gcloud secrets versions add RAZORPAY_KEY_SECRET --data-file=-
echo -n "<actual_webhook_secret>" | gcloud secrets versions add RAZORPAY_WEBHOOK_SECRET --data-file=-

# Grant Cloud Run service account read access to all three secrets
gcloud secrets add-iam-policy-binding RAZORPAY_KEY_ID \
  --member="serviceAccount:<cloud-run-sa>@<project>.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding RAZORPAY_KEY_SECRET \
  --member="serviceAccount:<cloud-run-sa>@<project>.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding RAZORPAY_WEBHOOK_SECRET \
  --member="serviceAccount:<cloud-run-sa>@<project>.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

For **local development**, add these to your `.env` file:
```
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret_here
```

### Step 7 — Apply the Supabase Database Migration

The `payment_transactions` table is declared with `managed = False` in Django — Django will **not** create it automatically. You must apply the migration manually before starting the server.

Run each statement individually via the Supabase CLI (per the project convention in `system_design.md`):

```bash
supabase db query "CREATE TABLE IF NOT EXISTS public.payment_transactions (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), reservation_id UUID REFERENCES public.reservations(id) ON DELETE SET NULL, order_id UUID REFERENCES public.orders(id) ON DELETE SET NULL, user_id UUID NOT NULL, razorpay_order_id TEXT UNIQUE NOT NULL, razorpay_payment_id TEXT, razorpay_signature TEXT, amount_paise INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'INR', status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'attempted', 'paid', 'failed', 'refunded')), failure_reason TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());"

supabase db query "CREATE INDEX IF NOT EXISTS idx_payment_txn_rp_order ON public.payment_transactions(razorpay_order_id);"

supabase db query "CREATE INDEX IF NOT EXISTS idx_payment_txn_user ON public.payment_transactions(user_id);"
```

Also apply the Order model schema patches (new `payment_method` and `payment_status` choices):

```bash
supabase db query "ALTER TABLE public.orders DROP CONSTRAINT IF EXISTS orders_payment_method_check;"
supabase db query "ALTER TABLE public.orders ADD CONSTRAINT orders_payment_method_check CHECK (payment_method IN ('UPI', 'card', 'cash', 'online'));"
supabase db query "ALTER TABLE public.orders DROP CONSTRAINT IF EXISTS orders_payment_status_check;"
supabase db query "ALTER TABLE public.orders ADD CONSTRAINT orders_payment_status_check CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded'));"
```

Save the SQL snippet to `supabase/snippets/` as `003_payment_transactions.sql`.

### Step 8 — Patch `cloudbuild.yaml` to Inject Razorpay Secrets

Add the three new secrets to the `--update-secrets` flag on the Cloud Run deploy step:

```yaml
'--update-secrets=DATABASE_URL=django_settings:latest,JWT_SECRET_KEY=django_settings:latest,RAZORPAY_KEY_ID=RAZORPAY_KEY_ID:latest,RAZORPAY_KEY_SECRET=RAZORPAY_KEY_SECRET:latest,RAZORPAY_WEBHOOK_SECRET=RAZORPAY_WEBHOOK_SECRET:latest'
```

### Pre-Requisite Checklist

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | Razorpay merchant account created | Business/Dev | ☐ |
| 2 | KYC documents submitted | Business | ☐ |
| 3 | KYC approved by Razorpay | Razorpay | ☐ |
| 4 | Settlement bank account linked | Business | ☐ |
| 5 | Test Mode API keys generated & saved | Dev | ☐ |
| 6 | Live Mode API keys generated & saved (post-KYC) | Dev | ☐ |
| 7 | Webhook URL registered with 3 event types | Dev | ☐ |
| 8 | Webhook Secret saved immediately | Dev | ☐ |
| 9 | All 3 secrets added to Google Secret Manager | Dev | ☐ |
| 10 | Cloud Run SA granted `secretAccessor` on all 3 secrets | Dev | ☐ |
| 11 | `payment_transactions` table created in Supabase | Dev | ☐ |
| 12 | `orders` table constraints updated (new payment choices) | Dev | ☐ |
| 13 | `.env` updated with test credentials for local dev | Dev | ☐ |
| 14 | ngrok or test webhook simulator configured for local webhook testing | Dev | ☐ |
| 15 | `cloudbuild.yaml` patched to inject 3 new secrets | Dev | ☐ |

---

## Relationship to Existing Specs

| Spec | Impact |
|---|---|
| **Spec 07** (Checkout Reservation) | No changes — reservation creation is unchanged |
| **Spec 08** (Order Confirmation) | `POST /api/v1/orders/confirm/` is **deprecated** for online payments. It remains valid only for `cash` POS transactions. New Razorpay-backed flow replaces it for `UPI` and `card` |
| **Spec 04** (Database Models) | `Order` model gains new fields: `razorpay_order_id`, `razorpay_payment_id`, `razorpay_signature` |
| **Spec 13** (ONDC) | ONDC `/confirm` action will route through the same payment verification service layer |

---

## Scope

New Django app: `payments/`

### Endpoints

| Method | Path | Actor | Description |
|---|---|---|---|
| `POST` | `/api/v1/payments/create-order/` | Authenticated customer | Creates a Razorpay Order tied to an active reservation |
| `POST` | `/api/v1/payments/verify/` | Authenticated customer | Verifies payment signature; commits stock atomically |
| `POST` | `/api/v1/payments/webhook/` | Razorpay servers | Authoritative webhook for async payment events |
| `POST` | `/api/v1/payments/refund/` | Staff / Manager only | Initiates refund via Razorpay API + reverses stock |
| `GET` | `/api/v1/payments/status/<uuid:order_id>/` | Authenticated (owner or staff) | Returns payment status for a given order |

---

## Files to Create / Modify

### `payments/` — New Django app

```bash
python manage.py startapp payments
```

Add to `INSTALLED_APPS`:
```python
'payments.apps.PaymentsConfig',
```

---

### `payments/models.py` — Payment transaction log

```python
import uuid
from django.db import models


class PaymentTransaction(models.Model):
    """
    Audit log for every payment attempt tied to a reservation.
    Separate from the Order model to allow multiple retries per reservation.
    """
    STATUS_CHOICES = [
        ('created', 'Created'),      # Razorpay order created, user hasn't paid yet
        ('attempted', 'Attempted'),  # User started payment, not yet confirmed
        ('paid', 'Paid'),            # Payment confirmed (signature verified or webhook)
        ('failed', 'Failed'),        # Payment failed or signature mismatch
        ('refunded', 'Refunded'),    # Full refund issued
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.ForeignKey(
        'inventory.Reservation',
        on_delete=models.SET_NULL,
        null=True,
        related_name='payment_transactions',
        db_column='reservation_id',
    )
    order = models.OneToOneField(
        'inventory.Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_transaction',
        db_column='order_id',
    )
    user_id = models.UUIDField()                          # Supabase auth.users(id)
    razorpay_order_id = models.TextField(unique=True)    # rp_order_XXXXXX
    razorpay_payment_id = models.TextField(null=True, blank=True)
    razorpay_signature = models.TextField(null=True, blank=True)
    amount_paise = models.IntegerField()                  # Amount in smallest unit (paise for INR)
    currency = models.TextField(default='INR')
    status = models.TextField(choices=STATUS_CHOICES, default='created')
    failure_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False     # Supabase owns DDL — add this table via a Supabase migration
        db_table = 'payment_transactions'

    def __str__(self):
        return f"{self.razorpay_order_id} | {self.status}"
```

---

### `payments/razorpay_client.py` — Razorpay SDK wrapper

```python
"""
Thin wrapper around the razorpay SDK to centralise client creation and keep
API calls mockable in unit tests.
"""
import hashlib
import hmac
import os

import razorpay

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')


def get_client() -> razorpay.Client:
    """Returns an authenticated Razorpay client."""
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


def create_razorpay_order(amount_paise: int, currency: str = 'INR', receipt: str = '') -> dict:
    """
    Creates a Razorpay Order. Returns the full Razorpay order dict.
    Raises razorpay.errors.BadRequestError on invalid inputs.
    """
    client = get_client()
    return client.order.create({
        'amount': amount_paise,
        'currency': currency,
        'receipt': receipt,            # Idempotency key — use reservation_id
        'payment_capture': 1,          # Auto-capture on payment success
    })


def verify_payment_signature(razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
    """
    Verifies the HMAC-SHA256 signature returned by the Razorpay frontend SDK.
    See: https://razorpay.com/docs/payments/server-integration/python/payment-gateway/build-integration/#14-verify-payment-signature
    """
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, razorpay_signature)


def verify_webhook_signature(webhook_body: bytes, webhook_signature: str, webhook_secret: str) -> bool:
    """
    Verifies Razorpay webhook signatures using the webhook secret (different from API secret).
    See: https://razorpay.com/docs/webhooks/validate-test/#validate-payment-related-webhooks
    """
    expected = hmac.new(
        webhook_secret.encode('utf-8'),
        webhook_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, webhook_signature)


def issue_refund(razorpay_payment_id: str, amount_paise: int, notes: dict = None) -> dict:
    """
    Issues a full or partial refund. Returns the Razorpay refund object.
    """
    client = get_client()
    payload = {'amount': amount_paise}
    if notes:
        payload['notes'] = notes
    return client.payment.refund(razorpay_payment_id, payload)
```

---

### `payments/views.py` — Core endpoint handlers

```python
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

from django.db import transaction, DatabaseError
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Reservation, Order, ProductVariant
from payments.models import PaymentTransaction
from payments.razorpay_client import (
    create_razorpay_order,
    verify_payment_signature,
    verify_webhook_signature,
    issue_refund,
)
from api.permissions import IsStaffOrManager

RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')


class CreatePaymentOrderView(APIView):
    """
    POST /api/v1/payments/create-order/

    Step 1 of the payment flow. Creates a Razorpay Order server-side and returns
    the Razorpay order_id + amount to the frontend, which uses it to render the
    Razorpay checkout widget.

    Expected body:
    {
        "reservation_id": "<uuid>"
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        reservation_id = request.data.get('reservation_id')
        if not reservation_id:
            return Response({'error': 'reservation_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            reservation_id = uuid.UUID(str(reservation_id))
        except ValueError:
            return Response({'error': 'reservation_id must be a valid UUID.'}, status=status.HTTP_400_BAD_REQUEST)

        user_id = uuid.UUID(request.user.username)

        try:
            reservation = Reservation.objects.select_related('variant__product').get(
                id=reservation_id,
                user_id=user_id,
                status='active',
            )
        except Reservation.DoesNotExist:
            return Response({'error': 'Active reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Guard: reservation must not be expired
        if reservation.expires_at < datetime.now(timezone.utc):
            reservation.status = 'expired'
            reservation.save(update_fields=['status'])
            return Response({'error': 'Reservation has expired. Please restart checkout.'}, status=status.HTTP_410_GONE)

        # Guard: idempotency — if a PaymentTransaction already exists for this reservation, return it
        existing = PaymentTransaction.objects.filter(
            reservation_id=reservation_id,
            status='created',
        ).first()
        if existing:
            return Response({
                'razorpay_order_id': existing.razorpay_order_id,
                'amount_paise': existing.amount_paise,
                'currency': existing.currency,
                'reservation_id': str(reservation_id),
            })

        # Compute total in paise (Razorpay uses smallest currency unit)
        variant = reservation.variant
        product = variant.product
        gst_rate = product.gst_slab / 100
        subtotal = variant.retail_price * reservation.reserved_quantity
        gst_amount = round(subtotal * gst_rate, 2)
        total_inr = round(subtotal + gst_amount, 2)
        amount_paise = int(total_inr * 100)   # Convert to paise

        try:
            rp_order = create_razorpay_order(
                amount_paise=amount_paise,
                currency='INR',
                receipt=str(reservation_id),   # Use reservation_id as idempotency receipt
            )
        except Exception as e:
            return Response({'error': f'Failed to create payment order: {str(e)}'}, status=status.HTTP_502_BAD_GATEWAY)

        # Persist the transaction record
        PaymentTransaction.objects.create(
            reservation_id=reservation_id,
            user_id=user_id,
            razorpay_order_id=rp_order['id'],
            amount_paise=amount_paise,
            currency='INR',
            status='created',
        )

        return Response({
            'razorpay_order_id': rp_order['id'],
            'razorpay_key_id': os.environ.get('RAZORPAY_KEY_ID', ''),  # Public key only — safe to send
            'amount_paise': amount_paise,
            'currency': 'INR',
            'reservation_id': str(reservation_id),
        }, status=status.HTTP_201_CREATED)


class VerifyPaymentView(APIView):
    """
    POST /api/v1/payments/verify/

    Step 2 of the payment flow. Called by the frontend AFTER Razorpay's JS SDK
    returns success. Verifies the HMAC-SHA256 signature, then atomically:
    - Decrements stock
    - Marks reservation as completed
    - Creates the Order record
    - Marks the PaymentTransaction as paid

    Expected body:
    {
        "razorpay_order_id": "order_XXXXXX",
        "razorpay_payment_id": "pay_XXXXXX",
        "razorpay_signature": "<hmac-sha256>"
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        razorpay_order_id = request.data.get('razorpay_order_id')
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_signature = request.data.get('razorpay_signature')

        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
            return Response(
                {'error': 'razorpay_order_id, razorpay_payment_id, and razorpay_signature are all required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 1: Verify HMAC signature ────────────────────────────────────
        if not verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
            return Response({'error': 'Payment signature verification failed.'}, status=status.HTTP_400_BAD_REQUEST)

        user_id = uuid.UUID(request.user.username)

        try:
            txn = PaymentTransaction.objects.select_related('reservation__variant__product').get(
                razorpay_order_id=razorpay_order_id,
                user_id=user_id,
            )
        except PaymentTransaction.DoesNotExist:
            return Response({'error': 'Payment transaction not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Idempotency: already confirmed
        if txn.status == 'paid' and txn.order_id:
            return Response({
                'order_id': str(txn.order_id),
                'payment_status': 'completed',
                'message': 'Payment already confirmed.',
            })

        # ── Step 2: Atomic commit ─────────────────────────────────────────────
        try:
            with transaction.atomic():
                reservation = Reservation.objects.select_for_update().get(
                    id=txn.reservation_id,
                    user_id=user_id,
                    status='active',
                )

                # Guard: reservation must not be expired
                if reservation.expires_at < datetime.now(timezone.utc):
                    reservation.status = 'expired'
                    reservation.save(update_fields=['status'])
                    txn.status = 'failed'
                    txn.failure_reason = 'Reservation expired before payment could be committed.'
                    txn.save(update_fields=['status', 'failure_reason'])
                    return Response({'error': 'Reservation expired.'}, status=status.HTTP_410_GONE)

                variant = ProductVariant.objects.select_for_update().get(id=reservation.variant_id)

                # Guard: physical stock must still be sufficient
                if variant.stock_quantity < reservation.reserved_quantity:
                    return Response({'error': 'Insufficient physical stock.'}, status=status.HTTP_409_CONFLICT)

                from django.db.models import F
                ProductVariant.objects.filter(id=variant.id).update(
                    stock_quantity=F('stock_quantity') - reservation.reserved_quantity
                )

                reservation.status = 'completed'
                reservation.save(update_fields=['status'])

                variant.refresh_from_db()
                product = variant.product
                gst_rate = product.gst_slab / 100
                subtotal = variant.retail_price * reservation.reserved_quantity
                gst_amount = round(subtotal * gst_rate, 2)
                total_amount = round(subtotal + gst_amount, 2)

                order = Order.objects.create(
                    user_id=user_id,
                    total_amount=total_amount,
                    gst_amount=gst_amount,
                    payment_method='online',    # UPI / card resolved by Razorpay
                    payment_status='completed',
                )

                txn.razorpay_payment_id = razorpay_payment_id
                txn.razorpay_signature = razorpay_signature
                txn.order_id = order.id
                txn.status = 'paid'
                txn.save(update_fields=['razorpay_payment_id', 'razorpay_signature', 'order_id', 'status'])

        except DatabaseError:
            return Response({'error': 'Database error. Please contact support.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({
            'order_id': str(order.id),
            'total_amount': str(total_amount),
            'gst_amount': str(gst_amount),
            'payment_status': 'completed',
            'razorpay_payment_id': razorpay_payment_id,
        }, status=status.HTTP_201_CREATED)


@method_decorator(csrf_exempt, name='dispatch')
class RazorpayWebhookView(APIView):
    """
    POST /api/v1/payments/webhook/

    Razorpay's authoritative async payment notification endpoint. Required for:
    - UPI collect flows (user pays asynchronously)
    - Auto-debit mandates
    - Net banking where the redirect may fail

    Razorpay calls this endpoint regardless of whether the frontend redirect succeeded.
    This is the GROUND TRUTH for payment completion — not the frontend verify call.

    Razorpay signs the request with HMAC-SHA256 using the Webhook Secret (not the API secret).
    """
    authentication_classes = []  # No JWT auth — Razorpay calls this endpoint
    permission_classes = []

    def post(self, request):
        webhook_signature = request.headers.get('X-Razorpay-Signature', '')
        if not verify_webhook_signature(request.body, webhook_signature, RAZORPAY_WEBHOOK_SECRET):
            return HttpResponse(status=400)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

        event = payload.get('event', '')

        if event == 'payment.captured':
            self._handle_payment_captured(payload)
        elif event == 'payment.failed':
            self._handle_payment_failed(payload)
        elif event == 'refund.created':
            self._handle_refund_created(payload)

        # Always return 200 to Razorpay — failure to do so causes webhook retries
        return HttpResponse(status=200)

    def _handle_payment_captured(self, payload: dict):
        """
        Idempotently commits the order on payment.captured.
        If verify/ already ran, this is a no-op (txn.status == 'paid').
        """
        payment = payload.get('payload', {}).get('payment', {}).get('entity', {})
        rp_order_id = payment.get('order_id')
        rp_payment_id = payment.get('id')

        try:
            txn = PaymentTransaction.objects.select_related('reservation__variant__product').get(
                razorpay_order_id=rp_order_id
            )
        except PaymentTransaction.DoesNotExist:
            return  # Unknown transaction — log and ignore

        if txn.status == 'paid':
            return  # Already committed via verify/ endpoint — idempotent no-op

        try:
            with transaction.atomic():
                reservation = Reservation.objects.select_for_update().get(
                    id=txn.reservation_id, status='active'
                )

                if reservation.expires_at < datetime.now(timezone.utc):
                    reservation.status = 'expired'
                    reservation.save(update_fields=['status'])
                    txn.status = 'failed'
                    txn.failure_reason = 'Reservation expired before webhook arrived.'
                    txn.save(update_fields=['status', 'failure_reason'])
                    # NOTE: Initiate refund here in production — Razorpay captured but stock is gone
                    return

                variant = ProductVariant.objects.select_for_update().get(id=reservation.variant_id)

                if variant.stock_quantity < reservation.reserved_quantity:
                    txn.status = 'failed'
                    txn.failure_reason = 'Insufficient physical stock at webhook time.'
                    txn.save(update_fields=['status', 'failure_reason'])
                    return

                from django.db.models import F
                ProductVariant.objects.filter(id=variant.id).update(
                    stock_quantity=F('stock_quantity') - reservation.reserved_quantity
                )
                reservation.status = 'completed'
                reservation.save(update_fields=['status'])

                product = variant.product
                gst_rate = product.gst_slab / 100
                subtotal = variant.retail_price * reservation.reserved_quantity
                gst_amount = round(subtotal * gst_rate, 2)
                total_amount = round(subtotal + gst_amount, 2)

                order = Order.objects.create(
                    user_id=txn.user_id,
                    total_amount=total_amount,
                    gst_amount=gst_amount,
                    payment_method='online',
                    payment_status='completed',
                )

                txn.razorpay_payment_id = rp_payment_id
                txn.order_id = order.id
                txn.status = 'paid'
                txn.save(update_fields=['razorpay_payment_id', 'order_id', 'status'])

        except Exception:
            pass  # Log to Cloud Logging in production — do not re-raise (200 must always return)

    def _handle_payment_failed(self, payload: dict):
        payment = payload.get('payload', {}).get('payment', {}).get('entity', {})
        rp_order_id = payment.get('order_id')
        error_description = payment.get('error_description', 'Unknown error')

        try:
            txn = PaymentTransaction.objects.get(razorpay_order_id=rp_order_id)
            if txn.status not in ('paid', 'refunded'):
                txn.status = 'failed'
                txn.failure_reason = error_description
                txn.save(update_fields=['status', 'failure_reason'])
        except PaymentTransaction.DoesNotExist:
            pass

    def _handle_refund_created(self, payload: dict):
        refund = payload.get('payload', {}).get('refund', {}).get('entity', {})
        rp_payment_id = refund.get('payment_id')

        try:
            txn = PaymentTransaction.objects.get(razorpay_payment_id=rp_payment_id)
            txn.status = 'refunded'
            txn.save(update_fields=['status'])
        except PaymentTransaction.DoesNotExist:
            pass


class RefundView(APIView):
    """
    POST /api/v1/payments/refund/

    Staff/Manager-only endpoint to issue a full refund and reverse the stock decrement atomically.

    Expected body:
    {
        "order_id": "<uuid>",
        "reason": "Customer requested cancellation"   // optional
    }
    """
    permission_classes = [IsAuthenticated, IsStaffOrManager]

    def post(self, request):
        order_id = request.data.get('order_id')
        reason = request.data.get('reason', '')

        if not order_id:
            return Response({'error': 'order_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            txn = PaymentTransaction.objects.select_related('order', 'reservation__variant').get(
                order_id=order_id,
                status='paid',
            )
        except PaymentTransaction.DoesNotExist:
            return Response({'error': 'No paid transaction found for this order.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            rp_refund = issue_refund(
                razorpay_payment_id=txn.razorpay_payment_id,
                amount_paise=txn.amount_paise,
                notes={'reason': reason, 'order_id': str(order_id)},
            )
        except Exception as e:
            return Response({'error': f'Razorpay refund failed: {str(e)}'}, status=status.HTTP_502_BAD_GATEWAY)

        # Reverse stock atomically
        try:
            with transaction.atomic():
                reservation = txn.reservation
                if reservation and reservation.status == 'completed':
                    from django.db.models import F
                    ProductVariant.objects.filter(id=reservation.variant_id).update(
                        stock_quantity=F('stock_quantity') + reservation.reserved_quantity
                    )

                txn.status = 'refunded'
                txn.save(update_fields=['status'])

                txn.order.payment_status = 'refunded'
                txn.order.save(update_fields=['payment_status'])

        except DatabaseError:
            # Refund issued but stock reversal failed — create an alert for manual resolution
            return Response({
                'warning': 'Refund issued but stock reversal failed. Manual inventory correction required.',
                'razorpay_refund_id': rp_refund.get('id'),
            }, status=status.HTTP_207_MULTI_STATUS)

        return Response({
            'razorpay_refund_id': rp_refund.get('id'),
            'amount_refunded_paise': rp_refund.get('amount'),
            'status': 'refunded',
        })


class PaymentStatusView(APIView):
    """
    GET /api/v1/payments/status/<uuid:order_id>/
    Returns the payment status for a given order. Owner or staff.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        user_id = uuid.UUID(request.user.username)
        role = getattr(request.user, 'role', 'customer')

        try:
            if role in ('staff', 'manager'):
                txn = PaymentTransaction.objects.get(order_id=order_id)
            else:
                txn = PaymentTransaction.objects.get(order_id=order_id, user_id=user_id)
        except PaymentTransaction.DoesNotExist:
            return Response({'error': 'Payment record not found.'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'order_id': str(order_id),
            'razorpay_order_id': txn.razorpay_order_id,
            'razorpay_payment_id': txn.razorpay_payment_id,
            'status': txn.status,
            'amount_paise': txn.amount_paise,
            'currency': txn.currency,
            'failure_reason': txn.failure_reason,
            'created_at': txn.created_at.isoformat(),
        })
```

---

### `payments/urls.py` — New file

```python
from django.urls import path
from payments.views import (
    CreatePaymentOrderView,
    VerifyPaymentView,
    RazorpayWebhookView,
    RefundView,
    PaymentStatusView,
)

urlpatterns = [
    path('payments/create-order/', CreatePaymentOrderView.as_view(), name='payment-create-order'),
    path('payments/verify/', VerifyPaymentView.as_view(), name='payment-verify'),
    path('payments/webhook/', RazorpayWebhookView.as_view(), name='payment-webhook'),
    path('payments/refund/', RefundView.as_view(), name='payment-refund'),
    path('payments/status/<uuid:order_id>/', PaymentStatusView.as_view(), name='payment-status'),
]
```

---

### `dwarikasbackend/urls.py` — Add payments routes

```python
path('api/v1/', include('payments.urls')),
```

---

### `inventory/models.py` — Patch Order model

Add `payment_method` choice `'online'` and `payment_status` choice `'refunded'`:

```python
PAYMENT_METHOD_CHOICES = [
    ('UPI', 'UPI'),
    ('card', 'Card'),
    ('cash', 'Cash'),
    ('online', 'Online'),   # NEW — covers Razorpay-managed UPI/card
]
PAYMENT_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('completed', 'Completed'),
    ('failed', 'Failed'),
    ('refunded', 'Refunded'),   # NEW
]
```

---

### Supabase Migration — New table

Add this SQL to a new Supabase migration file (`supabase/migrations/<timestamp>_add_payment_transactions.sql`):

```sql
CREATE TABLE IF NOT EXISTS public.payment_transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reservation_id  UUID REFERENCES public.reservations(id) ON DELETE SET NULL,
    order_id        UUID REFERENCES public.orders(id) ON DELETE SET NULL,
    user_id         UUID NOT NULL,
    razorpay_order_id   TEXT UNIQUE NOT NULL,
    razorpay_payment_id TEXT,
    razorpay_signature  TEXT,
    amount_paise    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'INR',
    status          TEXT NOT NULL DEFAULT 'created'
                    CHECK (status IN ('created', 'attempted', 'paid', 'failed', 'refunded')),
    failure_reason  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for webhook lookups by razorpay_order_id
CREATE INDEX IF NOT EXISTS idx_payment_txn_rp_order ON public.payment_transactions(razorpay_order_id);

-- Index for user-level payment history
CREATE INDEX IF NOT EXISTS idx_payment_txn_user ON public.payment_transactions(user_id);
```

---

### Environment Variables Required

| Variable | Description |
|---|---|
| `RAZORPAY_KEY_ID` | Razorpay API Key ID (public — safe to send to frontend) |
| `RAZORPAY_KEY_SECRET` | Razorpay API Key Secret (must NEVER leave the server) |
| `RAZORPAY_WEBHOOK_SECRET` | Separate secret for verifying webhook signatures |

Store all three in **Google Secret Manager** following the pattern established in Spec 02.

---

## Full Payment Flow Sequence

```
Customer Browser                Django Backend              Razorpay
     |                               |                          |
     |-- POST /payments/create-order/ -->                       |
     |   { reservation_id }          |                          |
     |                        create_razorpay_order() -------->|
     |                               |<------- { order_id } ---|
     |<-- { razorpay_order_id,        |                          |
     |     razorpay_key_id,           |                          |
     |     amount_paise }             |                          |
     |                               |                          |
     |  [User completes payment in Razorpay widget]             |
     |                               |                          |
     |<-- { payment_id, signature } --|-- payment.captured -->  |
     |                               |                          |
     |-- POST /payments/verify/ ----->|                          |
     |   { order_id, payment_id,      |                          |
     |     signature }               |                          |
     |                        verify_payment_signature()        |
     |                        [atomic: stock--, order++]        |
     |<-- { order_id, status: 'completed' }                     |
     |                               |                          |
     |                        POST /payments/webhook/ <---------|
     |                        (idempotent no-op if paid)        |
```

---

## Spec 08 Compatibility

The existing `POST /api/v1/orders/confirm/` endpoint in Spec 08 remains active **only for cash (POS) transactions**. For cash payments, no Razorpay order is created. Staff or manager must pass `payment_method: "cash"`. This endpoint must be updated to reject `UPI` and `card` payment methods and redirect callers to the new payment flow.

```python
# In OrderConfirmView.post() — add at the top of input validation:
if payment_method in ('UPI', 'card'):
    return Response(
        {'error': 'Online payments must use POST /api/v1/payments/create-order/ instead.'},
        status=status.HTTP_400_BAD_REQUEST,
    )
```

---

## Dependencies

Add to `requirements.txt`:

```
razorpay>=1.4.1
```

---

## Acceptance Criteria

- [ ] `POST /api/v1/payments/create-order/` without JWT returns 401
- [ ] Creating an order with an expired reservation returns 410
- [ ] Creating an order twice for the same reservation returns the existing Razorpay `order_id` (idempotent)
- [ ] `POST /api/v1/payments/verify/` with an invalid signature returns 400 (no stock decrement)
- [ ] `POST /api/v1/payments/verify/` with a valid signature atomically decrements stock, creates Order, marks transaction as `paid`
- [ ] Calling verify twice with the same `razorpay_order_id` is idempotent (returns the existing order)
- [ ] `POST /api/v1/payments/webhook/` with an invalid Razorpay-Signature returns 400
- [ ] Webhook `payment.captured` is idempotent — if `verify/` already ran, the webhook is a no-op
- [ ] Webhook `payment.failed` marks the transaction as `failed`
- [ ] `POST /api/v1/payments/refund/` by a customer returns 403
- [ ] `POST /api/v1/payments/refund/` by staff calls Razorpay API and reverses stock atomically
- [ ] `GET /api/v1/payments/status/<order_id>/` returns the transaction status for the owner
- [ ] `GET /api/v1/payments/status/<order_id>/` by staff returns status for any order
- [ ] `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` are loaded from Google Secret Manager, never hardcoded
- [ ] `POST /api/v1/orders/confirm/` with `payment_method: "UPI"` returns 400 directing to new flow

## Security Notes

- The webhook endpoint is `csrf_exempt` since Razorpay cannot send a CSRF token. Signature verification using `RAZORPAY_WEBHOOK_SECRET` provides equivalent protection.
- `RAZORPAY_KEY_ID` (the public key) is safe to return to the frontend; `RAZORPAY_KEY_SECRET` must never leave the server.
- `hmac.compare_digest` is used for all signature comparisons to prevent timing attacks.
- All amounts are computed server-side in paise from the database — the frontend never sends an amount.
