import jwt
from io import BytesIO
from PIL import Image
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant
from inventory.barcode_service import generate_code128, generate_ean13


def generate_test_jwt(role, user_id="user-123", email="user@example.com"):
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
class BarcodeGenerationTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Create a product and product variant for valid tests
        self.product = Product.objects.create(
            name="Silk Scarf",
            hsn_code="6214",
            gst_slab=18.00,
            brand="Dwarikas",
            category="Apparel"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="HS102-B2026",
            barcode="8901234567890",
            size="Medium",
            color="Silk Red",
            stock_quantity=15,
            retail_price=2499.00
        )

        # Generate tokens
        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

    def test_generate_code128_service_independently(self):
        """Test the standalone generate_code128 function."""
        # Standard generation
        buf = generate_code128("HS102-B2026")
        self.assertIsNotNone(buf)
        img = Image.open(buf)
        self.assertEqual(img.format, "PNG")
        self.assertTrue(img.width > 0 and img.height > 0)

        # With batch_id
        buf_with_batch = generate_code128("HS102-B2026", batch_id="B2026")
        self.assertIsNotNone(buf_with_batch)
        img2 = Image.open(buf_with_batch)
        self.assertEqual(img2.format, "PNG")
        self.assertTrue(img2.width > 0 and img2.height > 0)

    def test_generate_ean13_service_independently(self):
        """Test the standalone generate_ean13 function."""
        # 12 digits SKU
        buf = generate_ean13("123456789012")
        self.assertIsNotNone(buf)
        img = Image.open(buf)
        self.assertEqual(img.format, "PNG")
        self.assertTrue(img.width > 0 and img.height > 0)

        # SKU containing non-numeric should extract numbers, pad/truncate to 12
        buf_mixed = generate_ean13("SKU-1234-567")
        self.assertIsNotNone(buf_mixed)
        img_mixed = Image.open(buf_mixed)
        self.assertEqual(img_mixed.format, "PNG")

        # SKU with no numeric digits should raise ValueError
        with self.assertRaises(ValueError):
            generate_ean13("NONUMERIC")

    def test_code128_endpoint_returns_png_for_staff(self):
        """Staff/Manager roles can fetch Code 128 barcode."""
        url = reverse("barcode-code128", kwargs={"sku": self.variant.sku})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "no-store")

        # Verify it is a valid PNG
        buf = BytesIO(response.content)
        img = Image.open(buf)
        self.assertEqual(img.format, "PNG")
        self.assertTrue(img.width > 0 and img.height > 0)

    def test_code128_endpoint_returns_png_for_manager(self):
        """Manager role can fetch Code 128 barcode with query parameters."""
        url = reverse("barcode-code128", kwargs={"sku": self.variant.sku})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.get(f"{url}?batch_id=TESTBATCH")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")

        buf = BytesIO(response.content)
        img = Image.open(buf)
        self.assertEqual(img.format, "PNG")
        self.assertTrue(img.width > 0 and img.height > 0)

    def test_ean13_endpoint_returns_png_for_staff(self):
        """Staff/Manager roles can fetch EAN-13 barcode."""
        url = reverse("barcode-ean13", kwargs={"sku": self.variant.sku})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "no-store")

        buf = BytesIO(response.content)
        img = Image.open(buf)
        self.assertEqual(img.format, "PNG")
        self.assertTrue(img.width > 0 and img.height > 0)

    def test_unauthenticated_returns_401(self):
        """Request without token returns 401."""
        url_code128 = reverse("barcode-code128", kwargs={"sku": self.variant.sku})
        response = self.client.get(url_code128)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        url_ean13 = reverse("barcode-ean13", kwargs={"sku": self.variant.sku})
        response = self.client.get(url_ean13)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_returns_403(self):
        """Request with customer role token returns 403."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")

        url_code128 = reverse("barcode-code128", kwargs={"sku": self.variant.sku})
        response = self.client.get(url_code128)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        url_ean13 = reverse("barcode-ean13", kwargs={"sku": self.variant.sku})
        response = self.client.get(url_ean13)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unknown_sku_returns_404(self):
        """If variant doesn't exist, return 404."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")

        url_code128 = reverse("barcode-code128", kwargs={"sku": "UNKNOWN_SKU"})
        response = self.client.get(url_code128)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        url_ean13 = reverse("barcode-ean13", kwargs={"sku": "UNKNOWN_SKU"})
        response = self.client.get(url_ean13)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_ean13_invalid_numeric_sku_returns_422(self):
        """If SKU has no numbers, EAN-13 fails with 422."""
        # Create a product variant with non-numeric SKU for testing this view scenario
        non_numeric_variant = ProductVariant.objects.create(
            product=self.product,
            sku="NONUMERIC",
            barcode="8901234567899",
            stock_quantity=10,
            retail_price=10.0
        )

        url = reverse("barcode-ean13", kwargs={"sku": non_numeric_variant.sku})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("error", response.data)
        self.assertIn("contains no numeric characters", response.data["error"])
