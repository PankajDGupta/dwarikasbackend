import jwt
from datetime import datetime, timezone, timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant
from promotions.models import Promotion, PromotionItem


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
class PromotionAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Create sample products & variants
        self.product = Product.objects.create(
            name="Test Product",
            hsn_code="1234",
            gst_slab=18.00,
            brand="Dwarikas",
            category="Grocery",
            dietary_type="none"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-TEST-1",
            barcode="1111111111111",
            size="Medium",
            color="Red",
            stock_quantity=20,
            retail_price=100.00,
            mrp=120.00
        )

        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

        self.now = datetime.now(timezone.utc)

    def test_create_promotion_permissions(self):
        """Verify only Manager can create promotions."""
        url = reverse('promotion-list-create')
        payload = {
            "title": "Festival Sale",
            "description": "20% off during Diwali",
            "discount_type": "percentage",
            "discount_value": "20.00",
            "starts_at": (self.now - timedelta(days=1)).isoformat(),
            "ends_at": (self.now + timedelta(days=5)).isoformat(),
            "is_active": True
        }

        # 1. Anonymous request -> 401 Unauthorized
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer -> 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff -> 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 4. Manager -> 201 Created
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "Festival Sale")
        self.assertEqual(response.data["discount_type"], "percentage")

    def test_public_promotion_list_filtering(self):
        """Verify public list returns only currently live promotions."""
        # Active promotion
        promo_active = Promotion.objects.create(
            title="Active Promo",
            discount_type="percentage",
            discount_value=10.00,
            starts_at=self.now - timedelta(hours=1),
            ends_at=self.now + timedelta(hours=2),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        # Inactive promotion
        promo_inactive = Promotion.objects.create(
            title="Inactive Promo",
            discount_type="percentage",
            discount_value=15.00,
            starts_at=self.now - timedelta(hours=1),
            ends_at=self.now + timedelta(hours=2),
            is_active=False,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        # Future promotion
        promo_future = Promotion.objects.create(
            title="Future Promo",
            discount_type="percentage",
            discount_value=20.00,
            starts_at=self.now + timedelta(hours=1),
            ends_at=self.now + timedelta(hours=2),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        # Expired promotion
        promo_expired = Promotion.objects.create(
            title="Expired Promo",
            discount_type="percentage",
            discount_value=25.00,
            starts_at=self.now - timedelta(hours=2),
            ends_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )

        url = reverse('promotion-list-create')
        
        # Public anonymous request
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        # Should only return active live promotion
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Active Promo")

        # Manager request should see ALL promotions
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 4)

    def test_promotion_item_management(self):
        """Verify promotion items can be added and deleted by managers."""
        promo = Promotion.objects.create(
            title="Item Promo",
            discount_type="flat_amount",
            discount_value=15.00,
            starts_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )

        url = reverse('promotion-item-create', kwargs={"promotion_id": promo.id})
        
        # 1. Add product to promotion (Product level)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        payload_product = {"product_id": str(self.product.id)}
        response = self.client.post(url, data=payload_product)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item_id = response.data["id"]
        self.assertEqual(str(response.data["product_id"]), str(self.product.id))
        self.assertIsNone(response.data["variant_id"])

        # Check it is returned in detail view
        detail_url = reverse('promotion-detail', kwargs={"id": promo.id})
        response = self.client.get(detail_url)
        self.assertEqual(len(response.data["items"]), 1)

        # 2. Add variant to promotion (Variant level)
        payload_variant = {"variant_id": str(self.variant.id)}
        response = self.client.post(url, data=payload_variant)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        variant_item_id = response.data["id"]

        # Check detail view has 2 items now
        response = self.client.get(detail_url)
        self.assertEqual(len(response.data["items"]), 2)

        # 3. Delete promotion item
        delete_url = reverse('promotion-item-delete', kwargs={"promotion_id": promo.id, "item_id": item_id})
        response = self.client.delete(delete_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Check detail view has 1 item left
        response = self.client.get(detail_url)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(str(response.data["items"][0]["id"]), str(variant_item_id))
