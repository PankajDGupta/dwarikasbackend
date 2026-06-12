import jwt
from datetime import datetime, timezone as dt_timezone, timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient
from decimal import Decimal
import uuid
from django.utils import timezone

from coupons.models import Coupon
from coupons.services import validate_coupon, generate_coupon_code, create_gaming_reward_coupon


def generate_test_jwt(role, user_id="00000000-0000-0000-0000-000000000001", email="manager@example.com"):
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


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class CouponAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.manager_token = generate_test_jwt("manager")
        self.staff_token = generate_test_jwt("staff")
        self.customer_token = generate_test_jwt("customer")
        self.now = datetime.now(dt_timezone.utc)

    def test_create_coupon_permissions(self):
        """Only Managers can create coupons."""
        url = reverse('coupon-list-create')
        payload = {
            "code": "TEST50",
            "description": "50 off",
            "discount_type": "flat_amount",
            "discount_value": "50.00",
            "valid_from": (self.now - timedelta(days=1)).isoformat(),
            "valid_until": (self.now + timedelta(days=5)).isoformat(),
            "is_active": True
        }

        # Anonymous -> 401
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Staff -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Manager -> 201
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["code"], "TEST50")

    def test_list_and_detail_permissions(self):
        """Only Managers can list or retrieve coupon details."""
        coupon = Coupon.objects.create(
            code="WELCOME100",
            discount_type="flat_amount",
            discount_value=100.00,
            valid_from=self.now,
            created_by=uuid.uuid4()
        )

        list_url = reverse('coupon-list-create')
        detail_url = reverse('coupon-detail', kwargs={'id': coupon.id})

        # List permission tests
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.assertEqual(self.client.get(list_url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        self.assertEqual(self.client.get(list_url).status_code, status.HTTP_200_OK)

        # Detail permission tests
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_200_OK)

    def test_validate_coupon_rules(self):
        """Test coupon validation service rules."""
        user_id = uuid.uuid4()

        # 1. Non-existent coupon
        res = validate_coupon("NO_EXIST", user_id, Decimal("500.00"))
        self.assertFalse(res['valid'])
        self.assertEqual(res['error'], 'Invalid coupon code.')

        # 2. Inactive coupon
        coupon_inactive = Coupon.objects.create(
            code="INACTIVE",
            discount_type="flat_amount",
            discount_value=100.00,
            valid_from=self.now - timedelta(days=1),
            is_active=False,
            created_by=uuid.uuid4()
        )
        res = validate_coupon("INACTIVE", user_id, Decimal("500.00"))
        self.assertFalse(res['valid'])
        self.assertEqual(res['error'], 'This coupon is no longer active.')

        # 3. Not yet valid
        coupon_future = Coupon.objects.create(
            code="FUTURE",
            discount_type="flat_amount",
            discount_value=100.00,
            valid_from=self.now + timedelta(days=1),
            created_by=uuid.uuid4()
        )
        res = validate_coupon("FUTURE", user_id, Decimal("500.00"))
        self.assertFalse(res['valid'])
        self.assertEqual(res['error'], 'This coupon is not yet valid.')

        # 4. Expired
        coupon_expired = Coupon.objects.create(
            code="EXPIRED",
            discount_type="flat_amount",
            discount_value=100.00,
            valid_from=self.now - timedelta(days=2),
            valid_until=self.now - timedelta(days=1),
            created_by=uuid.uuid4()
        )
        res = validate_coupon("EXPIRED", user_id, Decimal("500.00"))
        self.assertFalse(res['valid'])
        self.assertEqual(res['error'], 'This coupon has expired.')

        # 5. User-specific mismatch
        other_user = uuid.uuid4()
        coupon_specific = Coupon.objects.create(
            code="SPECIFIC",
            discount_type="flat_amount",
            discount_value=100.00,
            valid_from=self.now - timedelta(days=1),
            specific_user_id=other_user,
            created_by=uuid.uuid4()
        )
        res = validate_coupon("SPECIFIC", user_id, Decimal("500.00"))
        self.assertFalse(res['valid'])
        self.assertEqual(res['error'], 'This coupon is not valid for your account.')

        # 6. Min order value
        coupon_min_val = Coupon.objects.create(
            code="MINVAL",
            discount_type="flat_amount",
            discount_value=100.00,
            valid_from=self.now - timedelta(days=1),
            min_order_value=Decimal("1000.00"),
            created_by=uuid.uuid4()
        )
        res = validate_coupon("MINVAL", user_id, Decimal("500.00"))
        self.assertFalse(res['valid'])
        self.assertIn("Minimum order value", res['error'])

        # 7. Valid coupon validation & pricing calculation (flat amount)
        coupon_valid_flat = Coupon.objects.create(
            code="VALIDFLAT",
            discount_type="flat_amount",
            discount_value=100.00,
            valid_from=self.now - timedelta(days=1),
            created_by=uuid.uuid4()
        )
        res = validate_coupon("VALIDFLAT", user_id, Decimal("500.00"))
        self.assertTrue(res['valid'])
        self.assertEqual(res['discount_amount'], Decimal("100.00"))
        self.assertEqual(res['final_total'], Decimal("400.00"))

        # Flat amount cap test (cannot exceed cart total)
        res_cap = validate_coupon("VALIDFLAT", user_id, Decimal("50.00"))
        self.assertTrue(res_cap['valid'])
        self.assertEqual(res_cap['discount_amount'], Decimal("50.00"))
        self.assertEqual(res_cap['final_total'], Decimal("0.00"))

        # 8. Valid percentage coupon with cap
        coupon_percent = Coupon.objects.create(
            code="PERCENT",
            discount_type="percentage",
            discount_value=10.00,
            max_discount_cap=40.00,
            valid_from=self.now - timedelta(days=1),
            created_by=uuid.uuid4()
        )
        # 10% of 300 = 30 (under cap)
        res = validate_coupon("PERCENT", user_id, Decimal("300.00"))
        self.assertTrue(res['valid'])
        self.assertEqual(res['discount_amount'], Decimal("30.00"))
        self.assertEqual(res['final_total'], Decimal("270.00"))

        # 10% of 500 = 50 (capped at 40)
        res = validate_coupon("PERCENT", user_id, Decimal("500.00"))
        self.assertTrue(res['valid'])
        self.assertEqual(res['discount_amount'], Decimal("40.00"))
        self.assertEqual(res['final_total'], Decimal("460.00"))

    def test_coupon_code_generator_and_gaming_reward(self):
        """Test generate_coupon_code and create_gaming_reward_coupon helpers."""
        code = generate_coupon_code(prefix='GAME', length=6)
        self.assertTrue(code.startswith('GAME'))
        self.assertEqual(len(code), 10)

        user_id = uuid.uuid4()
        created_by = uuid.uuid4()
        reward_config = {
            'discount_type': 'flat_amount',
            'discount_value': 75.00,
            'valid_days': 5,
            'description': 'Beat Level 5 reward'
        }

        coupon = create_gaming_reward_coupon(user_id, reward_config, created_by)
        self.assertEqual(coupon.discount_type, 'flat_amount')
        self.assertEqual(coupon.discount_value, Decimal('75.00'))
        self.assertEqual(coupon.max_uses, 1)
        self.assertEqual(coupon.specific_user_id, user_id)
        self.assertEqual(coupon.source, 'gaming_reward')
        self.assertTrue(coupon.code.startswith('GAME'))
        self.assertAlmostEqual(
            coupon.valid_until,
            timezone.now() + timedelta(days=5),
            delta=timedelta(seconds=10)
        )
