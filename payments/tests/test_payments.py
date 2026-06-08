"""
Comprehensive test suite for Spec #17 — Payment Gateway Integration (Razorpay).

Coverage:
  - Unit tests for razorpay_client crypto functions (no real Razorpay calls)
  - Integration tests for all 5 payment endpoints:
    - POST /api/v1/payments/create-order/
    - POST /api/v1/payments/verify/
    - POST /api/v1/payments/webhook/
    - POST /api/v1/payments/refund/
    - GET  /api/v1/payments/status/<order_id>/

All Razorpay API calls are mocked via unittest.mock to avoid real network calls.
Signature verification is tested with known HMAC values.

Acceptance criteria covered (per spec):
  [AC-01] create-order without JWT returns 401
  [AC-02] Expired reservation returns 410
  [AC-03] Duplicate create-order returns existing order_id (idempotent)
  [AC-04] verify/ with invalid signature returns 400 (no stock decrement)
  [AC-05] verify/ with valid signature atomically decrements stock + creates Order
  [AC-06] verify/ called twice for same order returns existing order (idempotent)
  [AC-07] webhook with invalid signature returns 400
  [AC-08] webhook payment.captured is idempotent if verify/ already ran
  [AC-09] webhook payment.failed marks transaction as failed
  [AC-10] refund/ by customer returns 403
  [AC-11] refund/ by staff calls Razorpay + reverses stock atomically
  [AC-12] status/<order_id>/ returns status for owner
  [AC-13] status/<order_id>/ for staff returns any order status
  [AC-14] orders/confirm/ with payment_method UPI returns 400 → new flow

Spec-compatibility tests:
  [COMPAT-01] orders/confirm/ rejects UPI/card and redirects to payments flow
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import jwt
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Order, Product, ProductVariant, Reservation
from payments.models import PaymentTransaction
from payments.razorpay_client import (
    verify_payment_signature,
    verify_webhook_signature,
)


# ── Test constants ─────────────────────────────────────────────────────────────

CUSTOMER_UUID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
OTHER_UUID    = '11111111-2222-3333-4444-555555555555'
STAFF_UUID    = 'cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa'
MANAGER_UUID  = 'dddddddd-eeee-ffff-aaaa-bbbbbbbbbbbb'

TEST_KEY_SECRET = 'test_razorpay_secret_key'
TEST_WEBHOOK_SECRET = 'test_webhook_secret'

# Pre-computed HMAC for TEST_KEY_SECRET — used for valid signature tests
def _make_signature(order_id: str, payment_id: str, secret: str = TEST_KEY_SECRET) -> str:
    message = f"{order_id}|{payment_id}"
    return hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _make_webhook_signature(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(
        secret.encode('utf-8'),
        body,
        hashlib.sha256,
    ).hexdigest()


def _jwt(role, user_id=CUSTOMER_UUID, email='user@test.com'):
    """Generate a HS256 JWT matching SupabaseJWTAuthentication expectations."""
    payload = {
        'aud': 'authenticated',
        'sub': user_id,
        'email': email,
        'app_metadata': {'role': role},
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm='HS256')


# ── Unit tests: razorpay_client crypto functions ───────────────────────────────

class RazorpayClientCryptoTests(TestCase):
    """
    Tests for HMAC signature verification functions.
    No real Razorpay calls — all crypto is deterministic.
    """

    def test_verify_payment_signature_returns_true_for_valid_signature(self):
        order_id = 'order_TestOrder123'
        payment_id = 'pay_TestPayment456'
        sig = _make_signature(order_id, payment_id, TEST_KEY_SECRET)

        with patch('payments.razorpay_client.RAZORPAY_KEY_SECRET', TEST_KEY_SECRET):
            result = verify_payment_signature(order_id, payment_id, sig)

        self.assertTrue(result)

    def test_verify_payment_signature_returns_false_for_tampered_signature(self):
        order_id = 'order_TestOrder123'
        payment_id = 'pay_TestPayment456'
        bad_sig = 'aaaaaaaabbbbbbbbccccccccddddddddeeeeeeeeffffffff0000000011111111'

        with patch('payments.razorpay_client.RAZORPAY_KEY_SECRET', TEST_KEY_SECRET):
            result = verify_payment_signature(order_id, payment_id, bad_sig)

        self.assertFalse(result)

    def test_verify_payment_signature_returns_false_for_wrong_order_id(self):
        order_id = 'order_Correct'
        payment_id = 'pay_TestPayment'
        sig = _make_signature('order_Wrong', payment_id, TEST_KEY_SECRET)

        with patch('payments.razorpay_client.RAZORPAY_KEY_SECRET', TEST_KEY_SECRET):
            result = verify_payment_signature(order_id, payment_id, sig)

        self.assertFalse(result)

    def test_verify_webhook_signature_returns_true_for_valid_signature(self):
        body = b'{"event": "payment.captured"}'
        sig = _make_webhook_signature(body, TEST_WEBHOOK_SECRET)

        result = verify_webhook_signature(body, sig, TEST_WEBHOOK_SECRET)

        self.assertTrue(result)

    def test_verify_webhook_signature_returns_false_for_tampered_body(self):
        original_body = b'{"event": "payment.captured"}'
        tampered_body = b'{"event": "payment.captured", "extra": "tampered"}'
        sig = _make_webhook_signature(original_body, TEST_WEBHOOK_SECRET)

        result = verify_webhook_signature(tampered_body, sig, TEST_WEBHOOK_SECRET)

        self.assertFalse(result)

    def test_verify_webhook_signature_returns_false_for_wrong_secret(self):
        body = b'{"event": "payment.captured"}'
        sig = _make_webhook_signature(body, 'correct_secret')

        result = verify_webhook_signature(body, sig, 'wrong_secret')

        self.assertFalse(result)


# ── Base test class with shared fixtures ──────────────────────────────────────

@override_settings(
    SUPABASE_JWT_SECRET='test-jwt-secret-key-at-least-32-chars-long',
    RAZORPAY_KEY_ID='rzp_test_fake_key_id',
)
class PaymentBaseTestCase(TestCase):
    """Shared setUp with product, variant, reservation, and JWT tokens."""

    def setUp(self):
        self.client = APIClient()

        # Catalog fixtures
        self.product = Product.objects.create(
            name='Test Product',
            hsn_code='123456',
            gst_slab=Decimal('18.00'),
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku='SKU-PAY-TEST-001',
            stock_quantity=10,
            retail_price=Decimal('100.00'),
        )

        # Time helpers
        self.now = datetime.now(timezone.utc)

        # Active reservation for the customer
        self.reservation = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=2,
            expires_at=self.now + timedelta(minutes=10),
            status='active',
        )

        # Tokens
        self.customer_token = _jwt('customer', CUSTOMER_UUID)
        self.other_token = _jwt('customer', OTHER_UUID)
        self.staff_token = _jwt('staff', STAFF_UUID)
        self.manager_token = _jwt('manager', MANAGER_UUID)

        # URLs
        self.create_order_url = reverse('payment-create-order')
        self.verify_url = reverse('payment-verify')
        self.webhook_url = reverse('payment-webhook')
        self.refund_url = reverse('payment-refund')

    def _status_url(self, order_id):
        return reverse('payment-status', kwargs={'order_id': order_id})

    def _auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def _make_txn(self, rp_order_id='order_TestRPOrder', status='created'):
        """Helper to create a PaymentTransaction in the DB."""
        return PaymentTransaction.objects.create(
            reservation=self.reservation,
            user_id=uuid.UUID(CUSTOMER_UUID),
            razorpay_order_id=rp_order_id,
            amount_paise=23600,  # ₹236.00 (₹200 + 18% GST)
            currency='INR',
            status=status,
        )


# ── Tests: POST /api/v1/payments/create-order/ ────────────────────────────────

class CreatePaymentOrderTests(PaymentBaseTestCase):

    # [AC-01] Authentication
    def test_unauthenticated_returns_401(self):
        response = self.client.post(self.create_order_url, data={})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_reservation_id_returns_400(self):
        self._auth(self.customer_token)
        response = self.client.post(self.create_order_url, data={})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_invalid_uuid_returns_400(self):
        self._auth(self.customer_token)
        response = self.client.post(
            self.create_order_url,
            data={'reservation_id': 'not-a-valid-uuid'},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_reservation_returns_404(self):
        self._auth(self.customer_token)
        response = self.client.post(
            self.create_order_url,
            data={'reservation_id': str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_users_reservation_returns_404(self):
        """Cannot create payment order for another user's reservation."""
        self._auth(self.other_token)
        response = self.client.post(
            self.create_order_url,
            data={'reservation_id': str(self.reservation.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # [AC-02] Expired reservation
    def test_expired_reservation_returns_410(self):
        self.reservation.expires_at = self.now - timedelta(minutes=1)
        self.reservation.save(update_fields=['expires_at'])

        self._auth(self.customer_token)
        response = self.client.post(
            self.create_order_url,
            data={'reservation_id': str(self.reservation.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertIn('expired', response.data['error'].lower())

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'expired')

    # [AC-03] Idempotency
    def test_duplicate_create_order_returns_existing_razorpay_order_id(self):
        """Calling create-order twice for same reservation returns the existing one."""
        existing_txn = self._make_txn(rp_order_id='order_ExistingRPOrder')

        self._auth(self.customer_token)
        response = self.client.post(
            self.create_order_url,
            data={'reservation_id': str(self.reservation.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['razorpay_order_id'], 'order_ExistingRPOrder')

        # No new transaction created
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    # Success path
    @patch('payments.views.create_razorpay_order')
    def test_success_creates_transaction_and_returns_201(self, mock_create_order):
        mock_create_order.return_value = {'id': 'order_NewRPOrder001', 'status': 'created'}

        self._auth(self.customer_token)
        response = self.client.post(
            self.create_order_url,
            data={'reservation_id': str(self.reservation.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['razorpay_order_id'], 'order_NewRPOrder001')
        self.assertIn('razorpay_key_id', response.data)
        self.assertIn('amount_paise', response.data)
        self.assertEqual(response.data['currency'], 'INR')

        # Amount: ₹100 × 2 + 18% GST = ₹236 = 23600 paise
        self.assertEqual(response.data['amount_paise'], 23600)

        # PaymentTransaction must be created
        txn = PaymentTransaction.objects.get(razorpay_order_id='order_NewRPOrder001')
        self.assertEqual(str(txn.user_id), CUSTOMER_UUID)
        self.assertEqual(txn.amount_paise, 23600)
        self.assertEqual(txn.status, 'created')

    @patch('payments.views.create_razorpay_order')
    def test_razorpay_api_failure_returns_502(self, mock_create_order):
        mock_create_order.side_effect = Exception('Razorpay unavailable')

        self._auth(self.customer_token)
        response = self.client.post(
            self.create_order_url,
            data={'reservation_id': str(self.reservation.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn('error', response.data)


# ── Tests: POST /api/v1/payments/verify/ ──────────────────────────────────────

class VerifyPaymentTests(PaymentBaseTestCase):

    def setUp(self):
        super().setUp()
        self.rp_order_id = 'order_VerifyTest001'
        self.rp_payment_id = 'pay_VerifyPayment001'
        self.txn = self._make_txn(rp_order_id=self.rp_order_id)

    # [AC-04] Invalid signature
    @patch('payments.views.verify_payment_signature', return_value=False)
    def test_invalid_signature_returns_400_without_stock_decrement(self, _):
        self._auth(self.customer_token)
        response = self.client.post(self.verify_url, data={
            'razorpay_order_id': self.rp_order_id,
            'razorpay_payment_id': self.rp_payment_id,
            'razorpay_signature': 'bad_signature',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('signature', response.data['error'].lower())

        # Stock MUST NOT be decremented
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

    def test_missing_fields_returns_400(self):
        self._auth(self.customer_token)
        response = self.client.post(self.verify_url, data={
            'razorpay_order_id': self.rp_order_id,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_returns_401(self):
        response = self.client.post(self.verify_url, data={})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_transaction_not_found_for_user_returns_404(self):
        """Another user cannot verify a payment transaction they don't own."""
        self._auth(self.other_token)
        with patch('payments.views.verify_payment_signature', return_value=True):
            response = self.client.post(self.verify_url, data={
                'razorpay_order_id': self.rp_order_id,
                'razorpay_payment_id': self.rp_payment_id,
                'razorpay_signature': 'any',
            })
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # [AC-05] Valid signature — atomic commit
    @patch('payments.views.verify_payment_signature', return_value=True)
    def test_valid_signature_decrements_stock_creates_order_marks_paid(self, _):
        self._auth(self.customer_token)
        response = self.client.post(self.verify_url, data={
            'razorpay_order_id': self.rp_order_id,
            'razorpay_payment_id': self.rp_payment_id,
            'razorpay_signature': 'valid_sig',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify response fields
        self.assertIn('order_id', response.data)
        self.assertEqual(response.data['payment_status'], 'completed')
        self.assertEqual(response.data['total_amount'], '236.00')
        self.assertEqual(response.data['gst_amount'], '36.00')

        # Stock decremented: 10 - 2 = 8
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)

        # Reservation completed
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'completed')

        # Order created with correct amounts
        order = Order.objects.get(id=response.data['order_id'])
        self.assertEqual(order.payment_method, 'online')
        self.assertEqual(order.payment_status, 'completed')
        self.assertEqual(order.total_amount, Decimal('236.00'))

        # PaymentTransaction marked as paid
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'paid')
        self.assertEqual(self.txn.razorpay_payment_id, self.rp_payment_id)
        self.assertEqual(str(self.txn.order_id), str(order.id))

    # [AC-06] Idempotency
    @patch('payments.views.verify_payment_signature', return_value=True)
    def test_verify_called_twice_is_idempotent(self, _):
        # First call
        self._auth(self.customer_token)
        response1 = self.client.post(self.verify_url, data={
            'razorpay_order_id': self.rp_order_id,
            'razorpay_payment_id': self.rp_payment_id,
            'razorpay_signature': 'valid_sig',
        })
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)
        order_id_first = response1.data['order_id']

        # Second call with same order_id
        response2 = self.client.post(self.verify_url, data={
            'razorpay_order_id': self.rp_order_id,
            'razorpay_payment_id': self.rp_payment_id,
            'razorpay_signature': 'valid_sig',
        })
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.data['order_id'], order_id_first)
        self.assertIn('already confirmed', response2.data.get('message', ''))

        # Stock still only decremented once
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)

    @patch('payments.views.verify_payment_signature', return_value=True)
    def test_verify_expired_reservation_returns_410(self, _):
        self.reservation.expires_at = self.now - timedelta(minutes=1)
        self.reservation.save(update_fields=['expires_at'])

        self._auth(self.customer_token)
        response = self.client.post(self.verify_url, data={
            'razorpay_order_id': self.rp_order_id,
            'razorpay_payment_id': self.rp_payment_id,
            'razorpay_signature': 'valid_sig',
        })
        self.assertEqual(response.status_code, status.HTTP_410_GONE)

        # Transaction marked failed
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'failed')
        self.assertIn('expired', self.txn.failure_reason.lower())

        # Stock untouched
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

    @patch('payments.views.verify_payment_signature', return_value=True)
    def test_verify_insufficient_stock_returns_409(self, _):
        self.variant.stock_quantity = 1
        self.variant.save(update_fields=['stock_quantity'])

        self._auth(self.customer_token)
        response = self.client.post(self.verify_url, data={
            'razorpay_order_id': self.rp_order_id,
            'razorpay_payment_id': self.rp_payment_id,
            'razorpay_signature': 'valid_sig',
        })
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

        # No order created
        self.assertFalse(Order.objects.exists())

        # Stock untouched
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 1)


# ── Tests: POST /api/v1/payments/webhook/ ─────────────────────────────────────

class RazorpayWebhookTests(PaymentBaseTestCase):

    def setUp(self):
        super().setUp()
        self.rp_order_id = 'order_WebhookTest001'
        self.rp_payment_id = 'pay_WebhookPayment001'
        self.txn = self._make_txn(rp_order_id=self.rp_order_id)

    def _webhook_post(self, payload: dict, secret: str = TEST_WEBHOOK_SECRET):
        """Helper: POST webhook with correct HMAC signature."""
        body = json.dumps(payload).encode()
        sig = _make_webhook_signature(body, secret)
        return self.client.post(
            self.webhook_url,
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=sig,
        )

    def _captured_payload(self, order_id: str, payment_id: str) -> dict:
        return {
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': payment_id,
                        'order_id': order_id,
                        'status': 'captured',
                    }
                }
            }
        }

    def _failed_payload(self, order_id: str) -> dict:
        return {
            'event': 'payment.failed',
            'payload': {
                'payment': {
                    'entity': {
                        'order_id': order_id,
                        'error_description': 'Payment declined by bank',
                    }
                }
            }
        }

    def _refund_payload(self, payment_id: str) -> dict:
        return {
            'event': 'refund.created',
            'payload': {
                'refund': {
                    'entity': {
                        'payment_id': payment_id,
                    }
                }
            }
        }

    # [AC-07] Invalid webhook signature
    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_invalid_signature_returns_400(self):
        body = json.dumps({'event': 'payment.captured'}).encode()
        response = self.client.post(
            self.webhook_url,
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE='invalid_signature_here',
        )
        self.assertEqual(response.status_code, 400)

    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_missing_signature_header_returns_400(self):
        body = json.dumps({'event': 'payment.captured'}).encode()
        response = self.client.post(
            self.webhook_url,
            data=body,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_invalid_json_returns_400(self):
        body = b'not-valid-json'
        sig = _make_webhook_signature(body, TEST_WEBHOOK_SECRET)
        response = self.client.post(
            self.webhook_url,
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 400)

    # [AC-08] Idempotency — payment.captured no-op if verify/ already ran
    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_payment_captured_is_noop_if_already_paid(self):
        # Simulate verify/ already ran
        existing_order = Order.objects.create(
            user_id=uuid.UUID(CUSTOMER_UUID),
            total_amount=Decimal('236.00'),
            gst_amount=Decimal('36.00'),
            payment_method='online',
            payment_status='completed',
        )
        self.txn.status = 'paid'
        self.txn.razorpay_payment_id = self.rp_payment_id
        self.txn.order_id = existing_order.id
        self.txn.save()

        # Mark reservation completed already
        self.reservation.status = 'completed'
        self.reservation.save(update_fields=['status'])
        self.variant.stock_quantity = 8  # Already decremented
        self.variant.save(update_fields=['stock_quantity'])

        payload = self._captured_payload(self.rp_order_id, self.rp_payment_id)
        response = self._webhook_post(payload)

        self.assertEqual(response.status_code, 200)

        # Stock must NOT be double-decremented
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)

        # Only one order exists
        self.assertEqual(Order.objects.count(), 1)

    # payment.captured — new commit path
    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_payment_captured_commits_order_when_not_yet_paid(self):
        payload = self._captured_payload(self.rp_order_id, self.rp_payment_id)
        response = self._webhook_post(payload)

        self.assertEqual(response.status_code, 200)

        # Transaction must be paid
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'paid')
        self.assertEqual(self.txn.razorpay_payment_id, self.rp_payment_id)

        # Order created
        self.assertIsNotNone(self.txn.order_id)
        order = Order.objects.get(id=self.txn.order_id)
        self.assertEqual(order.payment_status, 'completed')

        # Stock decremented: 10 - 2 = 8
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)

    # [AC-09] payment.failed
    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_payment_failed_marks_transaction_as_failed(self):
        payload = self._failed_payload(self.rp_order_id)
        response = self._webhook_post(payload)

        self.assertEqual(response.status_code, 200)

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'failed')
        self.assertEqual(self.txn.failure_reason, 'Payment declined by bank')

    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_payment_failed_does_not_overwrite_paid_status(self):
        """A payment.failed event for an already-paid txn must be ignored."""
        self.txn.status = 'paid'
        self.txn.save(update_fields=['status'])

        payload = self._failed_payload(self.rp_order_id)
        self._webhook_post(payload)

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'paid')  # Must not change

    # refund.created
    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_refund_created_marks_transaction_as_refunded(self):
        self.txn.razorpay_payment_id = self.rp_payment_id
        self.txn.status = 'paid'
        self.txn.save(update_fields=['razorpay_payment_id', 'status'])

        payload = self._refund_payload(self.rp_payment_id)
        response = self._webhook_post(payload)

        self.assertEqual(response.status_code, 200)

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'refunded')

    @patch('payments.views.RAZORPAY_WEBHOOK_SECRET', TEST_WEBHOOK_SECRET)
    def test_unknown_event_returns_200_noop(self):
        """Unknown events are silently acknowledged."""
        payload = {'event': 'subscription.charged', 'payload': {}}
        response = self._webhook_post(payload)
        self.assertEqual(response.status_code, 200)


# ── Tests: POST /api/v1/payments/refund/ ──────────────────────────────────────

class RefundTests(PaymentBaseTestCase):

    def setUp(self):
        super().setUp()
        # Create a completed order and associated paid transaction
        self.reservation.status = 'completed'
        self.reservation.save(update_fields=['status'])

        self.order = Order.objects.create(
            user_id=uuid.UUID(CUSTOMER_UUID),
            total_amount=Decimal('236.00'),
            gst_amount=Decimal('36.00'),
            payment_method='online',
            payment_status='completed',
        )

        self.txn = PaymentTransaction.objects.create(
            reservation=self.reservation,
            order=self.order,
            user_id=uuid.UUID(CUSTOMER_UUID),
            razorpay_order_id='order_RefundTest001',
            razorpay_payment_id='pay_RefundPayment001',
            amount_paise=23600,
            currency='INR',
            status='paid',
        )

        self.variant.stock_quantity = 8  # Already decremented
        self.variant.save(update_fields=['stock_quantity'])

    # [AC-10] Customer cannot issue refund
    def test_customer_cannot_issue_refund_returns_403(self):
        self._auth(self.customer_token)
        response = self.client.post(self.refund_url, data={'order_id': str(self.order.id)})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_returns_401(self):
        response = self.client.post(self.refund_url, data={})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_order_id_returns_400(self):
        self._auth(self.staff_token)
        response = self.client.post(self.refund_url, data={})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_paid_transaction_returns_404(self):
        self._auth(self.staff_token)
        response = self.client.post(
            self.refund_url,
            data={'order_id': str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # [AC-11] Staff can issue refund + stock reversal
    @patch('payments.views.issue_refund')
    def test_staff_refund_reverses_stock_and_marks_refunded(self, mock_refund):
        mock_refund.return_value = {'id': 'refund_TestRef001', 'amount': 23600}

        self._auth(self.staff_token)
        response = self.client.post(self.refund_url, data={
            'order_id': str(self.order.id),
            'reason': 'Customer requested cancellation',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['razorpay_refund_id'], 'refund_TestRef001')
        self.assertEqual(response.data['amount_refunded_paise'], 23600)

        # Stock reversed: 8 + 2 = 10
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

        # Transaction marked refunded
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'refunded')

        # Order marked refunded
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'refunded')

    @patch('payments.views.issue_refund')
    def test_manager_can_also_issue_refund(self, mock_refund):
        mock_refund.return_value = {'id': 'refund_MgrTest001', 'amount': 23600}

        self._auth(self.manager_token)
        response = self.client.post(self.refund_url, data={
            'order_id': str(self.order.id),
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch('payments.views.issue_refund')
    def test_razorpay_refund_failure_returns_502(self, mock_refund):
        mock_refund.side_effect = Exception('Razorpay unavailable')

        self._auth(self.staff_token)
        response = self.client.post(self.refund_url, data={
            'order_id': str(self.order.id),
        })
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

        # Transaction status must not change
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'paid')

        # Stock must not change
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)


# ── Tests: GET /api/v1/payments/status/<order_id>/ ────────────────────────────

class PaymentStatusTests(PaymentBaseTestCase):

    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            user_id=uuid.UUID(CUSTOMER_UUID),
            total_amount=Decimal('236.00'),
            gst_amount=Decimal('36.00'),
            payment_method='online',
            payment_status='completed',
        )
        self.txn = PaymentTransaction.objects.create(
            reservation=self.reservation,
            order=self.order,
            user_id=uuid.UUID(CUSTOMER_UUID),
            razorpay_order_id='order_StatusTest001',
            razorpay_payment_id='pay_StatusPayment001',
            amount_paise=23600,
            currency='INR',
            status='paid',
        )

    # [AC-12] Owner can see their own payment status
    def test_owner_can_view_own_payment_status(self):
        self._auth(self.customer_token)
        response = self.client.get(self._status_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data['order_id']), str(self.order.id))
        self.assertEqual(response.data['status'], 'paid')
        self.assertEqual(response.data['amount_paise'], 23600)
        self.assertIn('razorpay_order_id', response.data)
        self.assertIn('created_at', response.data)

    def test_owner_cannot_view_another_users_payment_status(self):
        self._auth(self.other_token)
        response = self.client.get(self._status_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # [AC-13] Staff can view any payment status
    def test_staff_can_view_any_payment_status(self):
        self._auth(self.staff_token)
        response = self.client.get(self._status_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data['order_id']), str(self.order.id))

    def test_manager_can_view_any_payment_status(self):
        self._auth(self.manager_token)
        response = self.client.get(self._status_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_nonexistent_order_returns_404(self):
        self._auth(self.customer_token)
        response = self.client.get(self._status_url(uuid.uuid4()))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self._status_url(self.order.id))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ── Tests: Spec 08 Compatibility ──────────────────────────────────────────────

@override_settings(SUPABASE_JWT_SECRET='test-jwt-secret-key-at-least-32-chars-long')
class OrderConfirmCompatibilityTests(TestCase):
    """
    [COMPAT-01] Spec 08 compatibility: orders/confirm/ must reject UPI/card
    and redirect to the new payments flow.
    """

    def setUp(self):
        self.client = APIClient()
        self.confirm_url = reverse('order-confirm')
        self.product = Product.objects.create(
            name='Compat Test Product',
            hsn_code='999999',
            gst_slab=Decimal('18.00'),
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku='SKU-COMPAT-001',
            stock_quantity=5,
            retail_price=Decimal('50.00'),
        )
        now = datetime.now(timezone.utc)
        self.reservation = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=1,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )
        self.customer_token = _jwt('customer', CUSTOMER_UUID)

    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.customer_token}')

    def test_confirm_with_upi_returns_400_with_redirect_message(self):
        self._auth()
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'UPI',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('create-order', response.data['error'])

    def test_confirm_with_card_returns_400_with_redirect_message(self):
        self._auth()
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'card',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('create-order', response.data['error'])

    def test_confirm_with_cash_still_works(self):
        """Cash payments via the POS flow must still succeed unchanged."""
        self._auth()
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'cash',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


# ── Tests: GST computation accuracy ───────────────────────────────────────────

@override_settings(
    SUPABASE_JWT_SECRET='test-jwt-secret-key-at-least-32-chars-long',
    RAZORPAY_KEY_ID='rzp_test_fake_key_id',
)
class GSTComputationTests(TestCase):
    """
    Verifies that GST computation and paise conversion are correct for various
    GST slabs — ensures no rounding errors affect payment amounts.
    """

    def setUp(self):
        self.client = APIClient()
        now = datetime.now(timezone.utc)

        # 5% GST slab (common for food items)
        self.product_5pct = Product.objects.create(
            name='Biscuit Pack', hsn_code='190531', gst_slab=Decimal('5.00')
        )
        self.variant_5pct = ProductVariant.objects.create(
            product=self.product_5pct,
            sku='SKU-GST-5PCT',
            stock_quantity=10,
            retail_price=Decimal('95.24'),   # Round number after 5% GST = ₹100
        )
        self.reservation_5pct = Reservation.objects.create(
            variant=self.variant_5pct,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=1,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )

        # 28% GST slab (luxury items)
        self.product_28pct = Product.objects.create(
            name='Luxury Item', hsn_code='870000', gst_slab=Decimal('28.00')
        )
        self.variant_28pct = ProductVariant.objects.create(
            product=self.product_28pct,
            sku='SKU-GST-28PCT',
            stock_quantity=10,
            retail_price=Decimal('100.00'),
        )
        self.reservation_28pct = Reservation.objects.create(
            variant=self.variant_28pct,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=1,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )

        self.customer_token = _jwt('customer', CUSTOMER_UUID)

    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.customer_token}')

    @patch('payments.views.create_razorpay_order')
    def test_5pct_gst_amount_paise_is_correct(self, mock_create):
        mock_create.return_value = {'id': 'order_GST5PCT', 'status': 'created'}
        self._auth()
        response = self.client.post(
            reverse('payment-create-order'),
            data={'reservation_id': str(self.reservation_5pct.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # ₹95.24 × 1 = ₹95.24; GST 5% = ₹4.76; Total = ₹100.00 → 10000 paise
        self.assertEqual(response.data['amount_paise'], 10000)

    @patch('payments.views.create_razorpay_order')
    def test_28pct_gst_amount_paise_is_correct(self, mock_create):
        mock_create.return_value = {'id': 'order_GST28PCT', 'status': 'created'}
        self._auth()
        response = self.client.post(
            reverse('payment-create-order'),
            data={'reservation_id': str(self.reservation_28pct.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # ₹100 × 1 = ₹100; GST 28% = ₹28; Total = ₹128 → 12800 paise
        self.assertEqual(response.data['amount_paise'], 12800)
