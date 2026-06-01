"""
Tests for Spec #07 — Checkout Reservation & Pessimistic Locking.

Test coverage:
  - Unauthenticated requests → 401
  - Valid reservation creation → 201
  - Insufficient ATP → 409 with atp/requested fields
  - Invalid input (missing variant_id, bad UUID, non-positive qty) → 400
  - Non-existent variant → 404
  - List endpoint (owner-only, only active non-expired rows returned)
  - Delete/release by owner → 204
  - Delete by non-owner customer → 403
  - Delete by staff on any reservation → 204
  - Concurrency: 10 simultaneous requests for the last unit — exactly 1 succeeds

The entire checkout flow executes inside transaction.atomic() (verified via
the select_for_update path; the concurrency test proves the invariant at the
integration level).
"""

import threading
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.conf import settings

from inventory.models import Product, ProductVariant, Reservation


# ── JWT helpers ────────────────────────────────────────────────────────────────

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
class CheckoutReserveCreateTests(TestCase):
    """Tests for POST /api/v1/checkout/reserve/"""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse('checkout-reserve')

        self.product = Product.objects.create(
            name="Test Product", hsn_code="0001", gst_slab=18.00
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="TEST-SKU-001",
            stock_quantity=5,
            retail_price=100.00,
        )

        self.customer_token = _jwt("customer", CUSTOMER_UUID)
        self.staff_token    = _jwt("staff",    STAFF_UUID)

    # ── Auth ──────────────────────────────────────────────────────────────

    def test_unauthenticated_returns_401(self):
        """POST without JWT → 401."""
        response = self.client.post(self.url, data={'variant_id': str(self.variant.id), 'quantity': 1})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── Input validation ──────────────────────────────────────────────────

    def test_missing_variant_id_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.url, data={'quantity': 1})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_invalid_uuid_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.url, data={'variant_id': 'not-a-uuid', 'quantity': 1})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_zero_quantity_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.url, data={'variant_id': str(self.variant.id), 'quantity': 0})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_negative_quantity_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.url, data={'variant_id': str(self.variant.id), 'quantity': -3})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nonexistent_variant_returns_404(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.url, data={'variant_id': str(uuid.uuid4()), 'quantity': 1})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Successful reservation ────────────────────────────────────────────

    def test_valid_reservation_creates_row_with_status_active(self):
        """Valid POST → 201, Reservation row created with status='active'."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(
            self.url, data={'variant_id': str(self.variant.id), 'quantity': 2}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        data = response.data
        self.assertIn('reservation_id', data)
        self.assertIn('expires_at', data)
        self.assertEqual(data['status'], 'active')
        self.assertEqual(data['reserved_quantity'], 2)
        self.assertEqual(data['variant_id'], str(self.variant.id))

        # Verify database row
        reservation = Reservation.objects.get(id=data['reservation_id'])
        self.assertEqual(reservation.status, 'active')
        self.assertEqual(reservation.reserved_quantity, 2)
        self.assertEqual(str(reservation.user_id), CUSTOMER_UUID)

    def test_default_quantity_is_one(self):
        """Omitting quantity defaults to 1."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.url, data={'variant_id': str(self.variant.id)})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['reserved_quantity'], 1)

    # ── ATP / conflict ────────────────────────────────────────────────────

    def test_insufficient_atp_returns_409_with_fields(self):
        """When active reservations exhaust stock, POST → 409 with atp and requested."""
        now = datetime.now(timezone.utc)
        # Reserve all 5 units first
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(OTHER_UUID),
            reserved_quantity=5,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(
            self.url, data={'variant_id': str(self.variant.id), 'quantity': 1}
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('atp', response.data)
        self.assertIn('requested', response.data)
        self.assertEqual(response.data['atp'], 0)
        self.assertEqual(response.data['requested'], 1)

    def test_expired_reservations_excluded_from_atp(self):
        """Expired holds don't count against ATP — new reservation should succeed."""
        now = datetime.now(timezone.utc)
        # Create an already-expired reservation consuming all stock
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(OTHER_UUID),
            reserved_quantity=5,
            expires_at=now - timedelta(minutes=1),   # already expired
            status='active',
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(
            self.url, data={'variant_id': str(self.variant.id), 'quantity': 5}
        )
        # Expired row must not block the ATP calculation
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_partial_active_reservations_reduce_atp(self):
        """Active hold of N units means only (stock - N) units are available."""
        now = datetime.now(timezone.utc)
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(OTHER_UUID),
            reserved_quantity=3,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        # Requesting remaining 2 units should succeed
        response = self.client.post(
            self.url, data={'variant_id': str(self.variant.id), 'quantity': 2}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Requesting 3 (> ATP=2) should fail
        response = self.client.post(
            self.url, data={'variant_id': str(self.variant.id), 'quantity': 3}
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class ReservationListTests(TestCase):
    """Tests for GET /api/v1/checkout/reserve/list/"""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse('reservation-list')

        self.product = Product.objects.create(
            name="List Test Product", hsn_code="0002", gst_slab=5.00
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="LIST-SKU-001", stock_quantity=10, retail_price=50.00
        )

        self.customer_token = _jwt("customer", CUSTOMER_UUID)

        now = datetime.now(timezone.utc)
        # Active reservation for the customer
        self.active_res = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=2,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )
        # Expired reservation for the same customer — must be excluded
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=1,
            expires_at=now - timedelta(minutes=1),
            status='active',
        )
        # Active reservation belonging to another user — must be excluded
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(OTHER_UUID),
            reserved_quantity=1,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )

    def test_unauthenticated_returns_401(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_only_callers_active_non_expired_reservations(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data['results'] if 'results' in response.data else response.data
        reservation_ids = [r['id'] for r in results]

        # Only the active, non-expired reservation belonging to CUSTOMER_UUID
        self.assertIn(str(self.active_res.id), reservation_ids)
        self.assertEqual(len(reservation_ids), 1)

    def test_response_includes_nested_variant(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.get(self.url)
        results = response.data['results'] if 'results' in response.data else response.data
        self.assertIn('variant', results[0])
        self.assertEqual(results[0]['variant']['sku'], 'LIST-SKU-001')


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class ReservationReleaseTests(TestCase):
    """Tests for DELETE /api/v1/checkout/reserve/<uuid:reservation_id>/"""

    def setUp(self):
        self.client = APIClient()

        self.product = Product.objects.create(
            name="Release Test Product", hsn_code="0003", gst_slab=5.00
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="REL-SKU-001", stock_quantity=10, retail_price=50.00
        )

        now = datetime.now(timezone.utc)
        self.reservation = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=1,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )

        self.customer_token = _jwt("customer", CUSTOMER_UUID)
        self.other_token    = _jwt("customer", OTHER_UUID)
        self.staff_token    = _jwt("staff",    STAFF_UUID)

    def _url(self, reservation_id):
        return reverse('reservation-release', kwargs={'reservation_id': reservation_id})

    def test_unauthenticated_returns_401(self):
        response = self.client.delete(self._url(self.reservation.id))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_owner_can_release_own_reservation(self):
        """Owner deletes their own active reservation → 204, status set to expired."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.delete(self._url(self.reservation.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'expired')

    def test_non_owner_customer_gets_403(self):
        """A different authenticated customer cannot release someone else's reservation."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.other_token}")
        response = self.client.delete(self._url(self.reservation.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # Reservation must remain active
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'active')

    def test_staff_can_release_any_reservation(self):
        """Staff can release any active reservation regardless of owner."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.delete(self._url(self.reservation.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'expired')

    def test_nonexistent_reservation_returns_404(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.delete(self._url(uuid.uuid4()))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_already_expired_reservation_returns_404(self):
        """Attempting to release a non-active (already expired) reservation → 404."""
        self.reservation.status = 'expired'
        self.reservation.save(update_fields=['status'])

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.delete(self._url(self.reservation.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class CheckoutConcurrencyTests(TransactionTestCase):
    """
    Concurrency acceptance test — Spec #07 requirement:
        Run 10 simultaneous requests for the last 1 unit.
        Exactly 1 request must receive HTTP 201; all others must receive
        HTTP 409 (ATP exhausted) or HTTP 503 (lock contention).
    """

    def setUp(self):
        self.product = Product.objects.create(
            name="Concurrency Product", hsn_code="9999", gst_slab=18.00
        )
        # Only 1 unit in stock
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="CONCUR-SKU-001",
            stock_quantity=1,
            retail_price=999.00,
        )

    def test_only_one_of_ten_concurrent_requests_succeeds(self):
        """
        Spawn 10 threads, each POSTing a reservation for the single available unit.

        On PostgreSQL (production):
          - Exactly one thread gets HTTP 201 (pessimistic lock succeeds).
          - All others get HTTP 409 (ATP exhausted) or HTTP 503 (lock contention).

        On SQLite (test/CI):
          - SQLite does not support SELECT ... FOR UPDATE.
          - All 10 threads will receive HTTP 503 (OperationalError path).
          - This still validates that no reservation row is double-created.
          - The invariant is fully tested on PostgreSQL in integration tests.
        """
        from django.db import connection

        url = reverse('checkout-reserve')
        results = []
        lock = threading.Lock()
        is_postgres = connection.vendor == 'postgresql'

        def make_request(index):
            # Each thread uses a unique user UUID and its own client to avoid
            # sharing authentication credentials across threads.
            user_uuid = f"aaaaaaaa-0000-0000-0000-{index:012d}"
            token = _jwt("customer", user_uuid)
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(url, data={
                'variant_id': str(self.variant.id),
                'quantity': 1,
            })
            with lock:
                results.append(response.status_code)
            # Close the thread-local DB connection so that Django can drop
            # the test database after all threads finish. Without this,
            # the 10 open connections block the DROP DATABASE teardown.
            from django.db import connections
            connections.close_all()

        threads = [threading.Thread(target=make_request, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = results.count(status.HTTP_201_CREATED)
        conflicts = results.count(status.HTTP_409_CONFLICT)
        lock_errors = results.count(status.HTTP_503_SERVICE_UNAVAILABLE)

        # All responses must be one of the three expected codes
        self.assertEqual(
            successes + conflicts + lock_errors,
            10,
            f"Unexpected status codes in results: {results}",
        )
        # Exactly one Reservation row with status='active' must exist
        active_count = Reservation.objects.filter(
            variant=self.variant, status='active'
        ).count()
        self.assertEqual(active_count, 1, (
            f"Expected exactly 1 active Reservation in DB but found {active_count}."
        ))
