"""
Payment endpoint views — Spec #17.

Endpoints implemented:
    POST /api/v1/payments/create-order/   — Step 1: create Razorpay Order server-side
    POST /api/v1/payments/verify/         — Step 2: verify HMAC signature + commit stock atomically
    POST /api/v1/payments/webhook/        — Razorpay authoritative async payment notification
    POST /api/v1/payments/refund/         — Staff-only: issue refund + reverse stock
    GET  /api/v1/payments/status/<order_id>/ — Payment status lookup

Security notes:
    - Webhook endpoint is csrf_exempt; Razorpay signs with HMAC-SHA256 (RAZORPAY_WEBHOOK_SECRET).
    - All amounts computed server-side from DB; frontend never sends an amount.
    - hmac.compare_digest() used for all signature comparisons (timing-attack safe).
    - RAZORPAY_KEY_SECRET is never returned to the frontend; only RAZORPAY_KEY_ID is.

References:
  - context/feature-spec/spec17-Payment_Gateway_Integration.md
"""

import json
import os
import uuid
from datetime import datetime, timezone

from django.db import transaction, DatabaseError
from django.db.models import F
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsStaffOrManager
from inventory.models import Order, ProductVariant, Reservation
from payments.models import PaymentTransaction
from payments.razorpay_client import (
    create_razorpay_order,
    issue_refund,
    verify_payment_signature,
    verify_webhook_signature,
)

RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')


# ── Helper: compute order totals from reservation + variant ────────────────────

def _compute_totals(variant, reservation):
    """Return (subtotal, gst_amount, total_inr, amount_paise) for a reservation."""
    product = variant.product
    gst_rate = product.gst_slab / 100
    if getattr(reservation, 'coupon_id', None) and reservation.final_price is not None:
        price = reservation.final_price
    else:
        price = reservation.effective_price if reservation.effective_price is not None else variant.retail_price
    subtotal = price * reservation.reserved_quantity
    gst_amount = round(subtotal * gst_rate, 2)
    total_inr = round(subtotal + gst_amount, 2)
    amount_paise = int(total_inr * 100)
    return subtotal, gst_amount, total_inr, amount_paise


# ── View 1: Create Razorpay Order ─────────────────────────────────────────────

class CreatePaymentOrderView(APIView):
    """
    POST /api/v1/payments/create-order/

    Step 1 of the online payment flow. Creates a Razorpay Order server-side and
    returns the razorpay_order_id + amount to the frontend so it can render the
    Razorpay checkout widget.

    The amount is computed here from the DB — the frontend never sends an amount.
    This enforces the invariant that client-side amount tampering is impossible.

    Expected body:
    {
        "reservation_id": "<uuid>"
    }

    Response (201):
    {
        "razorpay_order_id": "order_XXXXXX",
        "razorpay_key_id": "rzp_test_XXXXXX",   // Public key — safe to expose
        "amount_paise": 23600,
        "currency": "INR",
        "reservation_id": "<uuid>"
    }

    Idempotency: if a PaymentTransaction already exists for this reservation in
    'created' status, the existing Razorpay order_id is returned (no new order
    created on Razorpay).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        reservation_id_raw = request.data.get('reservation_id')
        if not reservation_id_raw:
            return Response(
                {'error': 'reservation_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reservation_id = uuid.UUID(str(reservation_id_raw))
        except (ValueError, AttributeError):
            return Response(
                {'error': 'reservation_id must be a valid UUID.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = uuid.UUID(request.user.username)

        # ── Load reservation (must be active and owned by caller) ────────────
        try:
            reservation = Reservation.objects.select_related(
                'variant__product'
            ).get(
                id=reservation_id,
                user_id=user_id,
                status='active',
            )
        except Reservation.DoesNotExist:
            return Response(
                {'error': 'Active reservation not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Guard: reservation must not be expired
        if reservation.expires_at < datetime.now(timezone.utc):
            reservation.status = 'expired'
            reservation.save(update_fields=['status'])
            return Response(
                {'error': 'Reservation has expired. Please restart checkout.'},
                status=status.HTTP_410_GONE,
            )

        # ── Idempotency: return existing transaction if already created ───────
        existing = PaymentTransaction.objects.filter(
            reservation_id=reservation_id,
            status='created',
        ).first()
        if existing:
            return Response({
                'razorpay_order_id': existing.razorpay_order_id,
                'razorpay_key_id': os.environ.get('RAZORPAY_KEY_ID', ''),
                'amount_paise': existing.amount_paise,
                'currency': existing.currency,
                'reservation_id': str(reservation_id),
            })

        # ── Compute total amount server-side ─────────────────────────────────
        variant = reservation.variant
        _, gst_amount, total_inr, amount_paise = _compute_totals(variant, reservation)

        # ── Create Razorpay Order ─────────────────────────────────────────────
        try:
            rp_order = create_razorpay_order(
                amount_paise=amount_paise,
                currency='INR',
                receipt=str(reservation_id),   # Idempotency receipt
            )
        except Exception as exc:
            return Response(
                {'error': f'Failed to create payment order: {str(exc)}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # ── Persist the transaction record ────────────────────────────────────
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
            'razorpay_key_id': os.environ.get('RAZORPAY_KEY_ID', ''),  # Public key — safe to send
            'amount_paise': amount_paise,
            'currency': 'INR',
            'reservation_id': str(reservation_id),
        }, status=status.HTTP_201_CREATED)


# ── View 2: Verify Payment Signature + Atomic Commit ─────────────────────────

class VerifyPaymentView(APIView):
    """
    POST /api/v1/payments/verify/

    Step 2 of the online payment flow. Called by the frontend AFTER Razorpay's
    JS SDK returns a success callback with payment credentials.

    Flow:
    1. Verify HMAC-SHA256 signature (razorpay_order_id|razorpay_payment_id).
    2. Inside transaction.atomic():
       a. SELECT FOR UPDATE on Reservation + ProductVariant (deadlock-safe order)
       b. Guard: reservation must still be active + not expired
       c. Guard: physical stock must still cover reserved_quantity
       d. Decrement stock via F() expression (avoids read-modify-write race)
       e. Mark reservation 'completed'
       f. Compute GST + create Order record
       g. Mark PaymentTransaction as 'paid'

    Expected body:
    {
        "razorpay_order_id": "order_XXXXXX",
        "razorpay_payment_id": "pay_XXXXXX",
        "razorpay_signature": "<hmac-sha256-hex>"
    }

    Response (201):
    {
        "order_id": "<uuid>",
        "total_amount": "236.00",
        "gst_amount": "36.00",
        "payment_status": "completed",
        "razorpay_payment_id": "pay_XXXXXX"
    }

    Idempotency: if the transaction is already 'paid', returns the existing order_id.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        razorpay_order_id = request.data.get('razorpay_order_id')
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_signature = request.data.get('razorpay_signature')

        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
            return Response(
                {
                    'error': (
                        'razorpay_order_id, razorpay_payment_id, and '
                        'razorpay_signature are all required.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Step 1: Verify HMAC signature ─────────────────────────────────────
        if not verify_payment_signature(
            razorpay_order_id, razorpay_payment_id, razorpay_signature
        ):
            return Response(
                {'error': 'Payment signature verification failed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = uuid.UUID(request.user.username)

        # ── Load the transaction record ───────────────────────────────────────
        try:
            txn = PaymentTransaction.objects.select_related(
                'reservation__variant__product'
            ).get(
                razorpay_order_id=razorpay_order_id,
                user_id=user_id,
            )
        except PaymentTransaction.DoesNotExist:
            return Response(
                {'error': 'Payment transaction not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Idempotency: already confirmed
        if txn.status == 'paid' and txn.order_id:
            return Response({
                'order_id': str(txn.order_id),
                'payment_status': 'completed',
                'message': 'Payment already confirmed.',
            })

        # ── Step 2: Atomic commit ─────────────────────────────────────────────
        order = None
        try:
            with transaction.atomic():
                # Lock ProductVariant first (parent), then Reservation (child)
                # — consistent ordering prevents deadlocks
                reservation_info = Reservation.objects.filter(
                    id=txn.reservation_id,
                    user_id=user_id,
                    status='active',
                ).values('variant_id').first()

                if not reservation_info:
                    return Response(
                        {'error': 'Active reservation not found. It may have already been used.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                variant = ProductVariant.objects.select_for_update().get(
                    id=reservation_info['variant_id']
                )
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
                    return Response(
                        {'error': 'Reservation expired.'},
                        status=status.HTTP_410_GONE,
                    )

                # Guard: physical stock must still be sufficient
                if variant.stock_quantity < reservation.reserved_quantity:
                    return Response(
                        {'error': 'Insufficient physical stock.'},
                        status=status.HTTP_409_CONFLICT,
                    )

                # Decrement stock atomically via F() expression
                ProductVariant.objects.filter(id=variant.id).update(
                    stock_quantity=F('stock_quantity') - reservation.reserved_quantity
                )

                # Mark reservation completed
                reservation.status = 'completed'
                reservation.save(update_fields=['status'])

                # Compute totals from DB (variant.product already loaded via select_related)
                variant.refresh_from_db()
                _, gst_amount, total_amount, _ = _compute_totals(variant, reservation)

                # Create Order record
                order = Order.objects.create(
                    user_id=user_id,
                    total_amount=total_amount,
                    gst_amount=gst_amount,
                    payment_method='online',    # Razorpay manages UPI/card internally
                    payment_status='completed',
                )

                if getattr(reservation, 'coupon_id', None):
                    from coupons.models import CouponRedemption
                    CouponRedemption.objects.filter(
                        reservation_id=reservation.id,
                        user_id=user_id,
                        order__isnull=True
                    ).update(order=order)

                # Mark transaction as paid
                txn.razorpay_payment_id = razorpay_payment_id
                txn.razorpay_signature = razorpay_signature
                txn.order_id = order.id
                txn.status = 'paid'
                txn.save(update_fields=[
                    'razorpay_payment_id', 'razorpay_signature', 'order_id', 'status'
                ])

        except Reservation.DoesNotExist:
            return Response(
                {'error': 'Active reservation not found. It may have already been used.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except DatabaseError:
            return Response(
                {'error': 'Database error. Please contact support.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            'order_id': str(order.id),
            'total_amount': str(total_amount),
            'gst_amount': str(gst_amount),
            'payment_status': 'completed',
            'razorpay_payment_id': razorpay_payment_id,
        }, status=status.HTTP_201_CREATED)


# ── View 3: Razorpay Webhook ──────────────────────────────────────────────────

@method_decorator(csrf_exempt, name='dispatch')
class RazorpayWebhookView(APIView):
    """
    POST /api/v1/payments/webhook/

    Razorpay's authoritative async payment notification endpoint.

    Required for:
      - UPI collect flows (user pays asynchronously)
      - Auto-debit mandates
      - Net banking where the redirect may fail

    Razorpay fires this endpoint regardless of whether the frontend redirect
    succeeded — it is the GROUND TRUTH for payment completion.

    Signature verification uses RAZORPAY_WEBHOOK_SECRET (separate from API secret).
    Always returns HTTP 200 — failure causes Razorpay to retry up to 3 days.

    Handled events:
      - payment.captured → idempotently commit order (no-op if verify/ already ran)
      - payment.failed   → mark transaction as failed
      - refund.created   → mark transaction as refunded
    """
    authentication_classes = []  # No JWT auth — Razorpay is the caller
    permission_classes = []

    def post(self, request):
        webhook_signature = request.headers.get('X-Razorpay-Signature', '')
        if not webhook_signature:
            return HttpResponse(status=400)

        if not verify_webhook_signature(
            request.body, webhook_signature, RAZORPAY_WEBHOOK_SECRET
        ):
            return HttpResponse(status=400)

        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return HttpResponse(status=400)

        event = payload.get('event', '')

        if event == 'payment.captured':
            self._handle_payment_captured(payload)
        elif event == 'payment.failed':
            self._handle_payment_failed(payload)
        elif event == 'refund.created':
            self._handle_refund_created(payload)
        # Unknown events are silently acknowledged (return 200)

        # Always return 200 — failure causes Razorpay to retry
        return HttpResponse(status=200)

    def _handle_payment_captured(self, payload: dict):
        """
        Idempotently commits the order on payment.captured.
        If verify/ already ran, this is a no-op (txn.status == 'paid').
        """
        payment = payload.get('payload', {}).get('payment', {}).get('entity', {})
        rp_order_id = payment.get('order_id')
        rp_payment_id = payment.get('id')

        if not rp_order_id:
            return

        try:
            txn = PaymentTransaction.objects.select_related(
                'reservation__variant__product'
            ).get(razorpay_order_id=rp_order_id)
        except PaymentTransaction.DoesNotExist:
            return  # Unknown transaction — log in production, ignore here

        if txn.status == 'paid':
            return  # Already committed via verify/ — idempotent no-op

        try:
            with transaction.atomic():
                reservation_info = Reservation.objects.filter(
                    id=txn.reservation_id,
                    status='active',
                ).values('variant_id').first()

                if not reservation_info:
                    return

                variant = ProductVariant.objects.select_for_update().get(
                    id=reservation_info['variant_id']
                )
                reservation = Reservation.objects.select_for_update().get(
                    id=txn.reservation_id,
                    status='active',
                )

                if reservation.expires_at < datetime.now(timezone.utc):
                    reservation.status = 'expired'
                    reservation.save(update_fields=['status'])
                    txn.status = 'failed'
                    txn.failure_reason = 'Reservation expired before webhook arrived.'
                    txn.save(update_fields=['status', 'failure_reason'])
                    # NOTE: Razorpay has captured but stock is gone — initiate refund in production
                    return

                if variant.stock_quantity < reservation.reserved_quantity:
                    txn.status = 'failed'
                    txn.failure_reason = 'Insufficient physical stock at webhook time.'
                    txn.save(update_fields=['status', 'failure_reason'])
                    return

                ProductVariant.objects.filter(id=variant.id).update(
                    stock_quantity=F('stock_quantity') - reservation.reserved_quantity
                )
                reservation.status = 'completed'
                reservation.save(update_fields=['status'])

                variant.refresh_from_db()
                _, gst_amount, total_amount, _ = _compute_totals(variant, reservation)

                order = Order.objects.create(
                    user_id=txn.user_id,
                    total_amount=total_amount,
                    gst_amount=gst_amount,
                    payment_method='online',
                    payment_status='completed',
                )

                if getattr(reservation, 'coupon_id', None):
                    from coupons.models import CouponRedemption
                    CouponRedemption.objects.filter(
                        reservation_id=reservation.id,
                        user_id=txn.user_id,
                        order__isnull=True
                    ).update(order=order)

                txn.razorpay_payment_id = rp_payment_id
                txn.order_id = order.id
                txn.status = 'paid'
                txn.save(update_fields=['razorpay_payment_id', 'order_id', 'status'])

        except Exception:
            # Log to Cloud Logging in production — must not re-raise (200 must always return)
            pass

    def _handle_payment_failed(self, payload: dict):
        """Marks the PaymentTransaction as failed."""
        payment = payload.get('payload', {}).get('payment', {}).get('entity', {})
        rp_order_id = payment.get('order_id')
        error_description = payment.get('error_description', 'Unknown error')

        if not rp_order_id:
            return

        try:
            txn = PaymentTransaction.objects.get(razorpay_order_id=rp_order_id)
            if txn.status not in ('paid', 'refunded'):
                txn.status = 'failed'
                txn.failure_reason = error_description
                txn.save(update_fields=['status', 'failure_reason'])
        except PaymentTransaction.DoesNotExist:
            pass

    def _handle_refund_created(self, payload: dict):
        """Marks the PaymentTransaction as refunded."""
        refund = payload.get('payload', {}).get('refund', {}).get('entity', {})
        rp_payment_id = refund.get('payment_id')

        if not rp_payment_id:
            return

        try:
            txn = PaymentTransaction.objects.get(razorpay_payment_id=rp_payment_id)
            txn.status = 'refunded'
            txn.save(update_fields=['status'])
        except PaymentTransaction.DoesNotExist:
            pass


# ── View 4: Staff Refund ───────────────────────────────────────────────────────

class RefundView(APIView):
    """
    POST /api/v1/payments/refund/

    Staff/Manager-only endpoint to issue a full refund and reverse the stock
    decrement atomically.

    Flow:
    1. Locate the paid PaymentTransaction for the order.
    2. Call Razorpay Refunds API (outside atomic block — network call).
    3. Inside transaction.atomic():
       a. Reverse stock via F() expression.
       b. Mark transaction as 'refunded'.
       c. Mark Order.payment_status as 'refunded'.

    If the Razorpay call succeeds but the DB update fails, we return 207
    with a warning so ops can manually reconcile.

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
            return Response(
                {'error': 'order_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            txn = PaymentTransaction.objects.select_related(
                'order', 'reservation__variant'
            ).get(
                order_id=order_id,
                status='paid',
            )
        except PaymentTransaction.DoesNotExist:
            return Response(
                {'error': 'No paid transaction found for this order.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── Call Razorpay Refunds API ─────────────────────────────────────────
        try:
            rp_refund = issue_refund(
                razorpay_payment_id=txn.razorpay_payment_id,
                amount_paise=txn.amount_paise,
                notes={'reason': reason, 'order_id': str(order_id)},
            )
        except Exception as exc:
            return Response(
                {'error': f'Razorpay refund failed: {str(exc)}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # ── Atomic stock reversal + status updates ────────────────────────────
        try:
            with transaction.atomic():
                reservation = txn.reservation
                if reservation and reservation.status == 'completed':
                    ProductVariant.objects.filter(
                        id=reservation.variant_id
                    ).update(
                        stock_quantity=F('stock_quantity') + reservation.reserved_quantity
                    )

                txn.status = 'refunded'
                txn.save(update_fields=['status'])

                txn.order.payment_status = 'refunded'
                txn.order.save(update_fields=['payment_status'])

        except DatabaseError:
            # Refund issued but stock reversal failed — alert ops for manual correction
            return Response({
                'warning': (
                    'Refund issued but stock reversal failed. '
                    'Manual inventory correction required.'
                ),
                'razorpay_refund_id': rp_refund.get('id'),
            }, status=status.HTTP_207_MULTI_STATUS)

        return Response({
            'razorpay_refund_id': rp_refund.get('id'),
            'amount_refunded_paise': rp_refund.get('amount'),
            'status': 'refunded',
        })


# ── View 5: Payment Status Lookup ─────────────────────────────────────────────

class PaymentStatusView(APIView):
    """
    GET /api/v1/payments/status/<uuid:order_id>/

    Returns the payment status for a given order.
    Owners can view only their own orders; staff/managers can view any.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        user_id = uuid.UUID(request.user.username)
        role = getattr(request.user, 'role', 'customer')

        try:
            if role in ('staff', 'manager'):
                txn = PaymentTransaction.objects.get(order_id=order_id)
            else:
                txn = PaymentTransaction.objects.get(
                    order_id=order_id,
                    user_id=user_id,
                )
        except PaymentTransaction.DoesNotExist:
            return Response(
                {'error': 'Payment record not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

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
