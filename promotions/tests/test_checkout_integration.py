import jwt
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant, Reservation, Order
from promotions.models import Promotion, PromotionItem


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


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class PromotionCheckoutIntegrationTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Create products and variants
        self.product = Product.objects.create(
            name="Silk Scarf",
            hsn_code="6214",
            gst_slab=12.00,
            brand="Dwarikas",
            category="Apparel"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DWA-SILK-SCARF",
            barcode="8901111111111",
            stock_quantity=10,
            retail_price=Decimal("1000.00"),
            mrp=Decimal("1200.00")
        )

        self.customer_token = generate_test_jwt("customer")
        self.manager_token = generate_test_jwt("manager")
        self.now = datetime.now(timezone.utc)

    def test_precedence_variant_vs_product_promotion(self):
        """Verify that when multiple promotions apply, the lowest price wins."""
        # 1. Product-level promotion (10% off -> effective price = 900)
        promo_prod = Promotion.objects.create(
            title="Product 10% Off",
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            starts_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        PromotionItem.objects.create(promotion=promo_prod, product=self.product)

        # 2. Variant-level promotion (₹150 flat off -> effective price = 850)
        promo_var = Promotion.objects.create(
            title="Variant 150 Off",
            discount_type="flat_amount",
            discount_value=Decimal("150.00"),
            starts_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        PromotionItem.objects.create(promotion=promo_var, variant=self.variant)

        # Resolve promotion for variant - Flat 150 off should win because 850 < 900
        from promotions.services import get_active_promotion_for_variant
        best_promo = get_active_promotion_for_variant(self.variant)
        self.assertEqual(best_promo["promotion_id"], str(promo_var.id))
        self.assertEqual(best_promo["effective_price"], Decimal("850.00"))
        self.assertEqual(best_promo["savings"], Decimal("150.00"))

        # 3. Create another product promotion with huge flat discount (₹300 off -> effective price = 700)
        promo_prod_best = Promotion.objects.create(
            title="Product 300 Off",
            discount_type="flat_amount",
            discount_value=Decimal("300.00"),
            starts_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        PromotionItem.objects.create(promotion=promo_prod_best, product=self.product)

        # Now product-level with 300 off wins because 700 < 850
        best_promo = get_active_promotion_for_variant(self.variant)
        self.assertEqual(best_promo["promotion_id"], str(promo_prod_best.id))
        self.assertEqual(best_promo["effective_price"], Decimal("700.00"))

    def test_percentage_discount_cap(self):
        """Verify that percentage discount respects max_discount_cap."""
        # 20% off with max cap ₹100. Product price is 1000. 20% = ₹200, but capped at ₹100.
        promo = Promotion.objects.create(
            title="Diwali 20% Off Capped",
            discount_type="percentage",
            discount_value=Decimal("20.00"),
            max_discount_cap=Decimal("100.00"),
            starts_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        PromotionItem.objects.create(promotion=promo, variant=self.variant)

        from promotions.services import get_active_promotion_for_variant
        best_promo = get_active_promotion_for_variant(self.variant)
        self.assertEqual(best_promo["effective_price"], Decimal("900.00"))  # 1000 - 100
        self.assertEqual(best_promo["savings"], Decimal("100.00"))

    def test_checkout_reservation_and_order_calculation(self):
        """Verify promotion details are captured on checkout reservation and used on order confirmation."""
        # 15% off flat amount discount -> effective price = 850
        promo = Promotion.objects.create(
            title="Flat 150 Off",
            discount_type="flat_amount",
            discount_value=Decimal("150.00"),
            starts_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        PromotionItem.objects.create(promotion=promo, variant=self.variant)

        # Create checkout reservation hold
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        reserve_url = reverse('checkout-reserve')
        response = self.client.post(reserve_url, data={"variant_id": str(self.variant.id), "quantity": 2})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["effective_price"], "850.00")
        self.assertEqual(response.data["promotion_id"], str(promo.id))

        reservation_id = response.data["reservation_id"]
        reservation = Reservation.objects.get(id=reservation_id)
        self.assertEqual(reservation.effective_price, Decimal("850.00"))
        self.assertEqual(reservation.promotion_id, promo.id)

        # Confirm order (re-authenticate just in case)
        confirm_url = reverse('order-confirm')
        response = self.client.post(confirm_url, data={"reservation_id": reservation_id, "payment_method": "cash"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # GST calculation:
        # Effective unit price = 850.00
        # Qty = 2
        # Subtotal = 1700.00
        # GST Slab = 12%
        # GST Amount = 1700 * 0.12 = 204.00
        # Total Amount = 1904.00
        self.assertEqual(response.data["gst_amount"], "204.00")
        self.assertEqual(response.data["total_amount"], "1904.00")

        order = Order.objects.get(id=response.data["order_id"])
        self.assertEqual(order.total_amount, Decimal("1904.00"))
        self.assertEqual(order.gst_amount, Decimal("204.00"))

    def test_razorpay_payment_order_amount(self):
        """Verify that payment amount created for Razorpay uses effective_price."""
        # Flat ₹100 flat amount discount -> effective price = 900
        promo = Promotion.objects.create(
            title="Flat 100 Off",
            discount_type="flat_amount",
            discount_value=Decimal("100.00"),
            starts_at=self.now - timedelta(hours=1),
            is_active=True,
            created_by="00000000-0000-0000-0000-000000000001"
        )
        PromotionItem.objects.create(promotion=promo, variant=self.variant)

        # Create checkout reservation hold
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        reserve_url = reverse('checkout-reserve')
        response = self.client.post(reserve_url, data={"variant_id": str(self.variant.id), "quantity": 1})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        reservation_id = response.data["reservation_id"]

        # Call Razorpay create payment order endpoint
        from unittest.mock import patch
        with patch('payments.views.create_razorpay_order') as mock_create:
            # Mock Razorpay API response
            mock_create.return_value = {
                'id': 'order_test_123',
                'amount': 100800,  # 900 subtotal + 12% GST = 1008 INR = 100800 Paise
                'currency': 'INR'
            }
            create_payment_url = reverse('payment-create-order')
            response = self.client.post(create_payment_url, data={"reservation_id": reservation_id})
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            
            # Subtotal = 900
            # GST = 900 * 0.12 = 108
            # Total = 1008 INR = 100800 Paise
            mock_create.assert_called_once_with(
                amount_paise=100800,
                currency='INR',
                receipt=reservation_id
            )
            self.assertEqual(response.data["amount_paise"], 100800)
