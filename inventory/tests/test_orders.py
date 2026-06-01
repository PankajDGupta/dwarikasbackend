import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.conf import settings
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant, Reservation, Order


def _jwt(role, user_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", email="user@test.com"):
    """Generate a HS256 JWT matching the SupabaseJWTAuthentication expectations."""
    payload = {
        "aud": "authenticated",
        "sub": user_id,
        "email": email,
        "app_metadata": {"role": role},
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


CUSTOMER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_UUID    = "11111111-2222-3333-4444-555555555555"
STAFF_UUID    = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class OrderConfirmationTests(TestCase):
    """Tests for Spec 08 — Order Confirmation & Atomic Stock Decrement."""

    def setUp(self):
        self.client = APIClient()
        self.confirm_url = reverse('order-confirm')
        self.list_url = reverse('order-list')

        # Setup catalog items
        self.product = Product.objects.create(
            name="Test Product",
            hsn_code="123456",
            gst_slab=Decimal("18.00"),
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-ORDER-CONFIRM",
            stock_quantity=10,
            retail_price=Decimal("100.00"),
        )

        self.customer_token = _jwt("customer", CUSTOMER_UUID)
        self.other_token = _jwt("customer", OTHER_UUID)
        self.staff_token = _jwt("staff", STAFF_UUID)

        # Create active reservation for customer
        self.now = datetime.now(timezone.utc)
        self.reservation = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=2,
            expires_at=self.now + timedelta(minutes=10),
            status='active',
        )

    def _detail_url(self, order_id):
        return reverse('order-detail', kwargs={'id': order_id})

    # ── Authentication ────────────────────────────────────────────────────────

    def test_unauthenticated_requests_return_401(self):
        # Confirm endpoint
        response = self.client.post(self.confirm_url, data={})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # List endpoint
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Detail endpoint
        response = self.client.get(self._detail_url(uuid.uuid4()))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── Input Validation ──────────────────────────────────────────────────────

    def test_confirm_missing_fields_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        
        # Missing payment method
        response = self.client.post(self.confirm_url, data={'reservation_id': str(self.reservation.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

        # Missing reservation id
        response = self.client.post(self.confirm_url, data={'payment_method': 'UPI'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_confirm_invalid_payment_method_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'Bitcoin'
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_confirm_invalid_uuid_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.confirm_url, data={
            'reservation_id': 'invalid-uuid-string',
            'payment_method': 'UPI'
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    # ── Success Flow ──────────────────────────────────────────────────────────

    def test_confirm_success_decrements_stock_completes_reservation_creates_order(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'UPI'
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Check response structure
        data = response.data
        self.assertIn('order_id', data)
        self.assertEqual(data['payment_method'], 'UPI')
        self.assertEqual(data['payment_status'], 'completed')
        
        # Subtotal: 100 * 2 = 200. GST: 200 * 18% = 36. Total: 236
        self.assertEqual(data['total_amount'], '236.00')
        self.assertEqual(data['gst_amount'], '36.00')

        # Check database records
        # 1. Reservation must be completed
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'completed')

        # 2. Variant stock must be decremented: 10 - 2 = 8
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 8)

        # 3. Order record must exist in DB
        order = Order.objects.get(id=data['order_id'])
        self.assertEqual(str(order.user_id), CUSTOMER_UUID)
        self.assertEqual(order.total_amount, Decimal('236.00'))
        self.assertEqual(order.gst_amount, Decimal('36.00'))
        self.assertEqual(order.payment_method, 'UPI')
        self.assertEqual(order.payment_status, 'completed')

    # ── Expiry Guard ──────────────────────────────────────────────────────────

    def test_confirm_expired_reservation_returns_410_and_transitions_status(self):
        # Update expires_at to the past
        self.reservation.expires_at = self.now - timedelta(minutes=1)
        self.reservation.save(update_fields=['expires_at'])

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'card'
        })
        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertIn('expired', response.data['error'])

        # Reservation status in DB must transition to expired
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'expired')

        # Variant stock must NOT be decremented (remains 10)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

        # No order should have been created
        self.assertFalse(Order.objects.exists())

    # ── Ownership Guard ───────────────────────────────────────────────────────

    def test_confirm_someone_elses_reservation_returns_404(self):
        # Authenticate as OTHER_UUID
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.other_token}")
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'cash'
        })
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Reservation remains active
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'active')

        # Stock is untouched
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

        # No order created
        self.assertFalse(Order.objects.exists())

    # ── Stock Guard ───────────────────────────────────────────────────────────

    def test_confirm_insufficient_stock_returns_409(self):
        # Reduce variant physical stock directly below reserved quantity
        self.variant.stock_quantity = 1
        self.variant.save(update_fields=['stock_quantity'])

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.confirm_url, data={
            'reservation_id': str(self.reservation.id),
            'payment_method': 'card'
        })
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('Insufficient physical stock', response.data['error'])

        # Reservation must remain active
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'active')

        # Stock remains 1
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 1)

        # No order created
        self.assertFalse(Order.objects.exists())

    # ── Transaction & Rollback Integrity ──────────────────────────────────────

    def test_confirm_rollback_on_database_error(self):
        # We mock Order.objects.create to raise a DatabaseError to simulate database failure
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        
        with patch('inventory.models.Order.objects.create') as mock_create:
            mock_create.side_effect = DatabaseError("Simulated DB error")
            
            response = self.client.post(self.confirm_url, data={
                'reservation_id': str(self.reservation.id),
                'payment_method': 'card'
            })
            self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

        # Verify all changes are rolled back:
        # 1. Reservation must still be active
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'active')

        # 2. Stock must still be 10 (not decremented)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 10)

        # 3. No order was created
        self.assertFalse(Order.objects.exists())

    # ── List & Detail Authorization/Isolation ─────────────────────────────────

    def test_list_and_detail_permissions_and_isolation(self):
        # Create orders for customer and other user
        customer_order = Order.objects.create(
            user_id=uuid.UUID(CUSTOMER_UUID),
            total_amount=Decimal('100.00'),
            gst_amount=Decimal('18.00'),
            payment_method='UPI',
            payment_status='completed',
        )
        other_order = Order.objects.create(
            user_id=uuid.UUID(OTHER_UUID),
            total_amount=Decimal('50.00'),
            gst_amount=Decimal('9.00'),
            payment_method='cash',
            payment_status='completed',
        )

        # 1. Customer user list check
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Customer should see only their own order
        results = response.data['results'] if 'results' in response.data else response.data
        order_ids = [o['id'] for o in results]
        self.assertIn(str(customer_order.id), order_ids)
        self.assertNotIn(str(other_order.id), order_ids)
        self.assertEqual(len(order_ids), 1)

        # 2. Customer user detail check
        # Owner can retrieve their own
        response = self.client.get(self._detail_url(customer_order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(customer_order.id))

        # Owner cannot retrieve another customer's order (should return 404 because of queryset isolation)
        response = self.client.get(self._detail_url(other_order.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 3. Staff user list check (sees all orders)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results'] if 'results' in response.data else response.data
        order_ids = [o['id'] for o in results]
        self.assertIn(str(customer_order.id), order_ids)
        self.assertIn(str(other_order.id), order_ids)
        self.assertEqual(len(order_ids), 2)

        # 4. Staff user detail check (can view any order)
        response = self.client.get(self._detail_url(customer_order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        response = self.client.get(self._detail_url(other_order.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
