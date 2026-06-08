import jwt
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch, ANY

from promotions.models import Promotion, PromotionBroadcast


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
class PromotionWhatsAppBroadcastTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.manager_token = generate_test_jwt("manager")
        self.staff_token = generate_test_jwt("staff")
        self.now = datetime.now(timezone.utc)

        # Active promo
        self.promo_active = Promotion.objects.create(
            title="Active Diwali Special",
            description="Get flat discounts on silk sarees",
            discount_type="flat_amount",
            discount_value=Decimal("200.00"),
            starts_at=self.now - timedelta(hours=1),
            ends_at=self.now + timedelta(hours=5),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )

        # Inactive promo
        self.promo_inactive = Promotion.objects.create(
            title="Inactive Promo",
            discount_type="flat_amount",
            discount_value=Decimal("100.00"),
            starts_at=self.now - timedelta(hours=1),
            is_active=False,
            created_by="00000000-0000-0000-0000-000000000001"
        )

    @patch('promotions.whatsapp_service.send_cta_url_message')
    def test_successful_broadcast(self, mock_send_whatsapp):
        """Verify successful WhatsApp broadcast logs entries in PromotionBroadcast table."""
        url = reverse('promotion-share-whatsapp', kwargs={"id": self.promo_active.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")

        payload = {
            "phone_numbers": ["919876543210", "918765432109"],
            "store_url": "https://dwarikas.com/shop"
        }

        response = self.client.post(url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_sent"], 2)
        self.assertEqual(response.data["total_failed"], 0)

        # Check mock calls
        self.assertEqual(mock_send_whatsapp.call_count, 2)
        mock_send_whatsapp.assert_any_call(
            to="919876543210",
            body_text=ANY,  # can check partial text if desired
            button_text="Shop the Sale",
            url=f"https://dwarikas.com/shop/promotions/{self.promo_active.id}"
        )

        # Verify database logs
        broadcasts = PromotionBroadcast.objects.filter(promotion=self.promo_active)
        self.assertEqual(broadcasts.count(), 2)
        self.assertTrue(all(b.status == 'sent' for b in broadcasts))
        self.assertTrue(all(str(b.sent_by) == "00000000-0000-0000-0000-000000000001" for b in broadcasts))

    @patch('promotions.whatsapp_service.send_cta_url_message')
    def test_partial_failure_handling(self, mock_send_whatsapp):
        """Verify that a failure to send to one number does not abort the rest of the broadcast."""
        # Mock side effect: first success, second failure
        def send_side_effect(to, body_text, button_text, url):
            if to == "918765432109":
                raise Exception("API rate limit exceeded")
            return None

        mock_send_whatsapp.side_effect = send_side_effect

        url = reverse('promotion-share-whatsapp', kwargs={"id": self.promo_active.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")

        payload = {
            "phone_numbers": ["919876543210", "918765432109"]
        }

        response = self.client.post(url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_sent"], 1)
        self.assertEqual(response.data["total_failed"], 1)

        # Verify db logs have one sent and one failed
        sent_log = PromotionBroadcast.objects.get(phone_number="919876543210")
        self.assertEqual(sent_log.status, "sent")

        failed_log = PromotionBroadcast.objects.get(phone_number="918765432109")
        self.assertEqual(failed_log.status, "failed")
        self.assertEqual(failed_log.failure_reason, "API rate limit exceeded")

    def test_broadcast_validation_not_live(self):
        """Verify broadcast returns 422 if the promotion is not currently live."""
        url = reverse('promotion-share-whatsapp', kwargs={"id": self.promo_inactive.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")

        payload = {
            "phone_numbers": ["919876543210"]
        }

        response = self.client.post(url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("error", response.data)

    def test_broadcast_validation_too_many_numbers(self):
        """Verify broadcast returns 400 if phone numbers list exceeds 100."""
        url = reverse('promotion-share-whatsapp', kwargs={"id": self.promo_active.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")

        # 101 phone numbers
        phone_numbers = [f"91987654{i:04d}" for i in range(101)]
        payload = {
            "phone_numbers": phone_numbers
        }

        response = self.client.post(url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_broadcast_validation_missing_numbers(self):
        """Verify broadcast returns 400 if phone numbers list is empty."""
        url = reverse('promotion-share-whatsapp', kwargs={"id": self.promo_active.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")

        response = self.client.post(url, data={"phone_numbers": []}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post(url, data={}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_broadcast_role_permissions(self):
        """Verify only managers (IsManager) can invoke sharing."""
        url = reverse('promotion-share-whatsapp', kwargs={"id": self.promo_active.id})

        # Staff gets 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, data={"phone_numbers": ["919876543210"]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
