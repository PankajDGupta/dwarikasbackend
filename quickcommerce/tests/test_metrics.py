import jwt
import datetime
from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant
from quickcommerce.models import QCPurchaseOrder, QCPOLineItem, QCPlatformListing
from quickcommerce.metrics.calculator import get_operational_metrics

def generate_test_jwt(role):
    payload = {
        "aud": "authenticated",
        "sub": "00000000-0000-0000-0000-000000000001",
        "email": "manager@example.com",
        "app_metadata": {
            "role": role
        }
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@override_settings(
    SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long",
    IS_TESTING=True
)
class OperationalMetricsTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Seed grocery product & variant
        self.product = Product.objects.create(
            name="Organic Basmati Rice",
            hsn_code="1006",
            gst_slab=Decimal("5.00"),
            product_type="grocery",
            brand="Dwarika's Farms",
            category="Groceries"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DWRK-RICE-ORG-05",
            barcode="8901234567890",
            stock_quantity=600,
            retail_price=Decimal("499.00"),
            mrp=Decimal("550.00")
        )

        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

    def test_metrics_rbac(self):
        """Verify only manager role can access operational metrics."""
        url = reverse('quickcommerce-metrics')

        # 1. Staff -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Manager -> 200
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_metrics_computations(self):
        """Verify OTIF, Fill Rate, and IDM metrics mathematical accuracy."""
        # 1. Seed PO 1: On-Time AND In-Full (OTIF success)
        now = timezone_now()
        po1 = QCPurchaseOrder.objects.create(
            platform="blinkit",
            platform_po_id="PO-OTIF-OK",
            po_status="ASN_SENT",
            received_at=now,
            dispatched_at=now + datetime.timedelta(hours=4)
        )
        QCPOLineItem.objects.create(
            po=po1, variant=self.variant, platform_sku="DWRK-RICE-ORG-05",
            ordered_quantity=100, delivered_quantity=100
        )

        # 2. Seed PO 2: Late but In-Full (OTIF fail)
        po2 = QCPurchaseOrder.objects.create(
            platform="blinkit",
            platform_po_id="PO-OTIF-LATE",
            po_status="ASN_SENT",
            received_at=now,
            dispatched_at=now + datetime.timedelta(hours=30)  # > 24 hours
        )
        QCPOLineItem.objects.create(
            po=po2, variant=self.variant, platform_sku="DWRK-RICE-ORG-05",
            ordered_quantity=100, delivered_quantity=100
        )

        # 3. Seed PO 3: On-Time but Partial Quantity (OTIF fail)
        po3 = QCPurchaseOrder.objects.create(
            platform="blinkit",
            platform_po_id="PO-OTIF-PARTIAL",
            po_status="ASN_SENT",
            received_at=now,
            dispatched_at=now + datetime.timedelta(hours=5)
        )
        QCPOLineItem.objects.create(
            po=po3, variant=self.variant, platform_sku="DWRK-RICE-ORG-05",
            ordered_quantity=100, delivered_quantity=80  # Fails in-full
        )

        # 4. Seed listing with simulated discrepancy (for IDM)
        # Barcode ending with 0 -> no simulated discrepancy
        QCPlatformListing.objects.create(
            product=self.product,
            variant=self.variant,
            platform="blinkit",
            sync_status="ACTIVE"
        )

        # Calculate metrics directly
        metrics = get_operational_metrics(platform="blinkit")

        # Total POs: 3
        # On-time and In-full POs: 1 (po1 only)
        # OTIF Rate = (1/3) * 100 = 33.33%
        self.assertEqual(metrics["total_pos"], 3)
        self.assertEqual(metrics["otif_rate"], 33.33)

        # Total ordered quantity = 100 (po1) + 100 (po2) + 100 (po3) = 300
        # Total delivered quantity = 100 (po1) + 100 (po2) + 80 (po3) = 280
        # Fill Rate = (280/300) * 100 = 93.33%
        self.assertEqual(metrics["fill_rate"], 93.33)

        # IDM: variant barcode "8901234567890" ends with '0', sync_status 'ACTIVE' with no errors
        # -> discrepancy = 0. Physical stock = 600. IDM = (0/600)*100 = 0.0%
        self.assertEqual(metrics["inventory_discrepancy_margin"], 0.0)

        # If we change variant barcode to end with '9' -> discrepancy offset = 2
        # Physical stock = 600. IDM = (2/600)*100 = 0.33%
        self.variant.barcode = "8901234567899"
        self.variant.save()

        metrics_with_discrepancy = get_operational_metrics(platform="blinkit")
        self.assertEqual(metrics_with_discrepancy["inventory_discrepancy_margin"], 0.33)


def timezone_now():
    from django.utils import timezone
    return timezone.now()
