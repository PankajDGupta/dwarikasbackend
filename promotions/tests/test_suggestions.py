import jwt
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant, Reservation
from promotions.models import DiscountSuggestion, Promotion, PromotionItem


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
class DiscountSuggestionsAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Create products & variants
        self.product = Product.objects.create(
            name="Test Rice",
            hsn_code="1006",
            gst_slab=5.00,
            brand="Dwarikas",
            category="Grocery",
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DW-RICE-1KG",
            barcode="8901234567890",
            stock_quantity=50,
            retail_price=Decimal('100.00'),
            mrp=Decimal('120.00'),
        )

        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

        self.now = dj_timezone.now()

        # Seed some suggestions
        self.suggestion_critical = DiscountSuggestion.objects.create(
            variant=self.variant,
            product=self.product,
            discount_score=85,
            priority="critical",
            reason_summary="High stock & low sales",
            reasons={"stock_score": 80, "recency_score": 90},
            suggested_discount_type="percentage",
            suggested_discount_value=Decimal('20.00'),
            suggested_ends_days=7,
            current_stock=50,
            avg_monthly_sales=Decimal('2.5'),
            days_since_last_order=45,
            analysed_at=self.now,
            status="pending",
        )

    def test_suggestions_list_permissions(self):
        """Verify only Manager can view suggestions feed."""
        url = reverse('discount-suggestion-list')

        # 1. Anonymous request -> 401
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Manager -> 200
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["priority"], "critical")

    def test_suggestions_list_filtering(self):
        """VerifySuggestions list can be filtered by priority."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")

        # Seed high suggestion
        other_variant = ProductVariant.objects.create(
            product=self.product,
            sku="DW-RICE-2KG",
            barcode="8901234567891",
            stock_quantity=30,
            retail_price=Decimal('200.00'),
        )
        DiscountSuggestion.objects.create(
            variant=other_variant,
            product=self.product,
            discount_score=65,
            priority="high",
            reason_summary="Moderate stock dips",
            suggested_discount_type="percentage",
            suggested_discount_value=Decimal('10.00'),
            analysed_at=self.now,
            status="pending",
            current_stock=30,
        )

        url = reverse('discount-suggestion-list')

        # Filter: critical
        response = self.client.get(url, {"priority": "critical"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["priority"], "critical")

        # Filter: high
        response = self.client.get(url, {"priority": "high"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["priority"], "high")

    def test_suggestion_detail_endpoint(self):
        """Verify suggestion detail view succeeds for manager."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        url = reverse('discount-suggestion-detail', kwargs={"id": self.suggestion_critical.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["discount_score"], 85)

    def test_approve_suggestion_creates_promotion(self):
        """Verify approving a suggestion atomically launches a live Promotion."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        url = reverse('discount-suggestion-approve', kwargs={"id": self.suggestion_critical.id})

        # POST empty body to use defaults
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        promo_id = response.data["promotion_id"]
        promotion = Promotion.objects.get(id=promo_id)
        self.assertEqual(promotion.title, f"{self.product.name} — Critical Discount Offer")
        self.assertEqual(promotion.discount_value, Decimal('20.00'))
        self.assertTrue(promotion.is_currently_live())

        # Verify PromotionItem is created linking variant
        self.assertTrue(PromotionItem.objects.filter(promotion=promotion, variant=self.variant).exists())

        # Verify suggestion status changes to approved
        self.suggestion_critical.refresh_from_db()
        self.assertEqual(self.suggestion_critical.status, 'approved')
        self.assertEqual(self.suggestion_critical.approved_promotion, promotion)

    def test_approve_suggestion_with_overrides(self):
        """Verify managers can override promotion duration, discount value, and title."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        url = reverse('discount-suggestion-approve', kwargs={"id": self.suggestion_critical.id})

        payload = {
            "title": "Super Rice Special",
            "ends_days": 3,
            "discount_value": 15.50
        }

        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        promo_id = response.data["promotion_id"]
        promotion = Promotion.objects.get(id=promo_id)
        self.assertEqual(promotion.title, "Super Rice Special")
        self.assertEqual(promotion.discount_value, Decimal('15.50'))

        # Verify expiration matches ends_days override
        delta = promotion.ends_at - promotion.starts_at
        self.assertEqual(delta.days, 3)

    def test_dismiss_suggestion_snoozes(self):
        """Verify dismissing a suggestion snoozes it for 30 days."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        url = reverse('discount-suggestion-dismiss', kwargs={"id": self.suggestion_critical.id})

        response = self.client.post(url, data={"snooze_days": 15})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.suggestion_critical.refresh_from_db()
        self.assertEqual(self.suggestion_critical.status, 'dismissed')
        diff = self.suggestion_critical.dismissed_until - dj_timezone.now()
        self.assertAlmostEqual(diff.days, 15, delta=1)


@override_settings(DEBUG=False)
class TasksDiscountAnalysisSecurityTests(TestCase):

    def test_run_analysis_view_task_header_security(self):
        """Verify tasks worker endpoint is protected by task header check when DEBUG=False."""
        url = reverse('task-run-discount-analysis')

        # 1. Unauthenticated post without task header -> 403 Forbidden
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Post with task header -> 200 OK (simulate Cloud Tasks execution)
        # Note: task views are exempt from regular JWT auth as they are called by serverless queue
        response = self.client.post(url, HTTP_X_CLOUDTASKS_TASKNAME="task-id-abc")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")
