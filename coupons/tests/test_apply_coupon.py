import jwt
import uuid
import threading
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from django.test import TransactionTestCase, TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient
from django.db import connection, connections

from coupons.models import Coupon, CouponRedemption
from inventory.models import Product, ProductVariant, Reservation, Order
from payments.models import PaymentTransaction


def generate_test_jwt(role, user_id="00000000-0000-0000-0000-000000000001", email="user@example.com"):
    """Helper: generate a valid JWT payload for the test environment."""
    payload = {
        "aud": "authenticated",
        "sub": user_id,
        "email": email,
        "app_metadata": {
            "role": role
        }
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


CUSTOMER_ID_STR = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_ID_STR = "11111111-2222-3333-4444-555555555555"


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class ApplyCouponAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.customer_token = generate_test_jwt("customer", CUSTOMER_ID_STR)
        self.other_token = generate_test_jwt("customer", OTHER_ID_STR)

        self.now = datetime.now(timezone.utc)

        self.product = Product.objects.create(
            name="Coupon Integration Product", hsn_code="1234", gst_slab=18.00
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-COUP-1",
            stock_quantity=10,
            retail_price=100.00,
        )
        self.reservation = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_ID_STR),
            reserved_quantity=2,
            expires_at=self.now + timedelta(minutes=10),
            status='active',
        )

        self.coupon = Coupon.objects.create(
            code="SAVE20",
            discount_type="percentage",
            discount_value=20.00,
            valid_from=self.now - timedelta(days=1),
            created_by=uuid.uuid4(),
        )

        self.apply_url = reverse('coupon-apply')

    def test_apply_coupon_success(self):
        """POST /checkout/apply-coupon/ successfully updates reservation and inserts redemption."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        payload = {
            "reservation_id": str(self.reservation.id),
            "coupon_code": "save20"  # case-insensitive check
        }

        response = self.client.post(self.apply_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["coupon_code"], "SAVE20")
        self.assertEqual(response.data["discount_amount"], "40.00")  # 20% of 200 = 40
        self.assertEqual(response.data["final_total"], "160.00")

        # Verify DB changes
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.coupon_id, self.coupon.id)
        self.assertEqual(self.reservation.coupon_discount, Decimal("40.00"))
        # final_price per unit: 100 - (40 / 2) = 80
        self.assertEqual(self.reservation.final_price, Decimal("80.00"))

        # Verify redemption record
        redemption = CouponRedemption.objects.get(reservation=self.reservation)
        self.assertEqual(redemption.coupon_id, self.coupon.id)
        self.assertEqual(str(redemption.user_id), CUSTOMER_ID_STR)
        self.assertIsNone(redemption.order)

    def test_remove_coupon_success(self):
        """DELETE /checkout/remove-coupon/<id>/ clears reservation and deletes pending redemption."""
        # 1. First apply
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.client.post(self.apply_url, data={
            "reservation_id": str(self.reservation.id),
            "coupon_code": "SAVE20"
        })

        remove_url = reverse('coupon-remove', kwargs={'reservation_id': self.reservation.id})
        response = self.client.delete(remove_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify DB is cleared
        self.reservation.refresh_from_db()
        self.assertIsNone(self.reservation.coupon_id)
        self.assertIsNone(self.reservation.coupon_discount)
        self.assertIsNone(self.reservation.final_price)

        # Verify redemption is deleted
        self.assertFalse(CouponRedemption.objects.filter(reservation=self.reservation).exists())

    def test_order_confirm_with_coupon(self):
        """Cash checkout (/orders/confirm/) applies final coupon-discounted price and links redemption."""
        # 1. Apply coupon
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.client.post(self.apply_url, data={
            "reservation_id": str(self.reservation.id),
            "coupon_code": "SAVE20"
        })

        # 2. Confirm order
        confirm_url = reverse('order-confirm')
        payload = {
            "reservation_id": str(self.reservation.id),
            "payment_method": "cash"
        }
        response = self.client.post(confirm_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Final unit price = 80. Qty = 2. Subtotal = 160.
        # GST Rate = 18%. GST Amount = round(160 * 0.18, 2) = 28.80
        # Total Amount = round(160 + 28.80, 2) = 188.80
        self.assertEqual(response.data["total_amount"], "188.80")
        self.assertEqual(response.data["gst_amount"], "28.80")

        # Verify redemption is updated with the order
        order_id = uuid.UUID(response.data["order_id"])
        redemption = CouponRedemption.objects.get(reservation=self.reservation)
        self.assertEqual(redemption.order_id, order_id)

    def test_payment_totals_and_verify_integration(self):
        """Payments endpoints correctly reflect coupon discount and link redemptions on verify."""
        # 1. Apply coupon
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.client.post(self.apply_url, data={
            "reservation_id": str(self.reservation.id),
            "coupon_code": "SAVE20"
        })

        # 2. Check Create Payment Order totals
        from unittest.mock import patch
        create_payment_url = reverse('payment-create-order')
        
        with patch('payments.views.create_razorpay_order', return_value={'id': 'order_mock123'}):
            response = self.client.post(create_payment_url, data={"reservation_id": str(self.reservation.id)})
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data["amount_paise"], 18880)

            # 3. Verify Payment View (mock signature verification and test linkage)
            txn = PaymentTransaction.objects.get(razorpay_order_id=response.data["razorpay_order_id"])
            verify_url = reverse('payment-verify')

            with patch('payments.views.verify_payment_signature', return_value=True):
                verify_payload = {
                    "razorpay_order_id": txn.razorpay_order_id,
                    "razorpay_payment_id": "pay_test123456",
                    "razorpay_signature": "mock_signature"
                }
                res_verify = self.client.post(verify_url, data=verify_payload)
                self.assertEqual(res_verify.status_code, status.HTTP_201_CREATED)
                self.assertEqual(res_verify.data["total_amount"], "188.80")

                # Check that CouponRedemption is linked to the order
                order_id = uuid.UUID(res_verify.data["order_id"])
                redemption = CouponRedemption.objects.get(reservation=self.reservation)
                self.assertEqual(redemption.order_id, order_id)


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class CouponConcurrencyTests(TransactionTestCase):
    """
    Concurrency test verifying that atomic SELECT FOR UPDATE row-locking
    prevents double-redemption race conditions for a coupon with max_uses=1.
    """

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.product = Product.objects.create(
            name="Concur Product", hsn_code="9999", gst_slab=18.00
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-CONC-C",
            stock_quantity=20,
            retail_price=100.00,
        )

        # Max uses = 1
        self.coupon = Coupon.objects.create(
            code="LIMITED1",
            discount_type="flat_amount",
            discount_value=10.00,
            max_uses=1,
            valid_from=self.now - timedelta(days=1),
            created_by=uuid.uuid4(),
        )

    def test_concurrency_race_for_limited_coupon(self):
        """
        10 threads simultaneously attempt to apply the single-use coupon to 10 unique reservations.
        Exactly 1 attempt must succeed; 9 must fail.
        """
        # Create 10 reservations for 10 users
        reservations = []
        for i in range(10):
            user_uuid = f"aaaaaaaa-0000-0000-0000-{i:012d}"
            res = Reservation.objects.create(
                variant=self.variant,
                user_id=uuid.UUID(user_uuid),
                reserved_quantity=1,
                expires_at=self.now + timedelta(minutes=10),
                status='active',
            )
            reservations.append((user_uuid, res))

        results = []
        lock = threading.Lock()
        apply_url = reverse('coupon-apply')

        def attempt_apply(user_uuid, res_obj):
            token = generate_test_jwt("customer", user_uuid)
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(apply_url, data={
                "reservation_id": str(res_obj.id),
                "coupon_code": "LIMITED1"
            })
            with lock:
                results.append(response.status_code)
            # Clean up db connection
            connections.close_all()

        threads = []
        for user_uuid, res_obj in reservations:
            t = threading.Thread(target=attempt_apply, args=(user_uuid, res_obj))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = results.count(status.HTTP_200_OK)
        # Other codes could be 409 (Conflict on lock) or 400 (Validation failure)
        failures = results.count(status.HTTP_409_CONFLICT) + results.count(status.HTTP_400_BAD_REQUEST)

        self.assertEqual(successes, 1, f"Expected exactly 1 success, got: {results}")
        self.assertEqual(successes + failures, 10, f"Expected 10 total attempts, got results: {results}")

        # In DB, exactly 1 redemption should exist
        self.assertEqual(CouponRedemption.objects.filter(coupon=self.coupon).count(), 1)
