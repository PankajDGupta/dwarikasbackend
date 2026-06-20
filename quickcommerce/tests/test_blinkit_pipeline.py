import jwt
import json
from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant
from quickcommerce.models import QCPlatformListing, QCPlatformCredentials, QCWarehouseMapping, QCPurchaseOrder, QCPOLineItem

def generate_test_jwt(role):
    payload = {
        "aud": "authenticated",
        "sub": "00000000-0000-0000-0000-000000000001",
        "email": "staff@example.com",
        "app_metadata": {
            "role": role
        }
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@override_settings(
    SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long",
    IS_TESTING=True
)
class BlinkitIntegrationTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Seed Blinkit credentials
        QCPlatformCredentials.objects.create(
            platform='blinkit',
            blinkit_vendor_id="BLK-VND-001",
            blinkit_receiver_code="BLK-RCV-001",
            blinkit_webhook_secret="supersecret"
        )

        # Seed warehouse mapping
        QCWarehouseMapping.objects.create(
            platform='blinkit',
            internal_facility_code='BLR_03',
            platform_location_id='560067',
            mapping_type='pincode'
        )

        # Seed grocery product & variant
        self.product = Product.objects.create(
            name="Organic Basmati Rice",
            hsn_code="1006",
            gst_slab=Decimal("5.00"),
            product_type="grocery",
            brand="Dwarika's Farms",
            category="Groceries",
            image_url="https://example.com/rice.jpg"
        )
        # Variant with standard barcode matching Branch A simulation
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DWRK-RICE-ORG-05",
            barcode="8901234567890",
            stock_quantity=600,
            retail_price=Decimal("499.00"),
            mrp=Decimal("550.00")
        )

        self.staff_token = generate_test_jwt("staff")

    def test_blinkit_catalog_matching_branch_a(self):
        """Verify listing matches existing Blinkit catalog (Branch A)."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url = reverse('quickcommerce-listings-sync')
        payload = {
            "product_id": str(self.product.id),
            "platforms": ["blinkit"],
            "fssai_license": "10012345000001",
            "marketplace_config": {
                "blinkit": {
                    "vendor_id": "BLK-VND-001",
                    "pincodes": ["560067"],
                    "has_catalog_match": True  # Force Branch A
                }
            }
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["results"]["blinkit"]["status"], "ACTIVE")

        # Verify listing in DB
        listing = QCPlatformListing.objects.get(product=self.product, platform='blinkit')
        self.assertEqual(listing.sync_status, "ACTIVE")

    def test_blinkit_template_generation_branch_b(self):
        """Verify new SKU compiles template and transitions to PENDING_REVIEW (Branch B)."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        
        # Create a new product that won't trigger Branch A simulation
        new_prod = Product.objects.create(
            name="Exotic Saffron",
            hsn_code="0910",
            gst_slab=Decimal("5.00"),
            product_type="grocery",
            brand="Dwarika's Farms",
            image_url="https://example.com/saffron.jpg"
        )
        new_var = ProductVariant.objects.create(
            product=new_prod,
            sku="DWRK-SAFF-01",
            barcode="9991234567890",  # does not start with 890123456789
            stock_quantity=100,
            retail_price=Decimal("199.00"),
            mrp=Decimal("220.00")
        )

        url = reverse('quickcommerce-listings-sync')
        payload = {
            "product_id": str(new_prod.id),
            "platforms": ["blinkit"],
            "fssai_license": "10012345000001",
            "marketplace_config": {
                "blinkit": {
                    "vendor_id": "BLK-VND-001",
                    "pincodes": ["560067"],
                    "has_catalog_match": False
                }
            }
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["results"]["blinkit"]["status"], "PENDING_REVIEW")
        self.assertIsNotNone(response.data["results"]["blinkit"]["submission_guid"])

        # Check DB
        listing = QCPlatformListing.objects.get(product=new_prod, platform='blinkit')
        self.assertEqual(listing.sync_status, "PENDING_REVIEW")

    def test_blinkit_po_webhook_ingestion(self):
        """Verify PO webhook processing and warehouse routing."""
        url = reverse('quickcommerce-blinkit-po-webhook')
        payload = {
            "event": "PURCHASE_ORDER_CREATED",
            "po_id": "BLK-PO-2026-001",
            "vendor_id": "BLK-VND-001",
            "items": [
                {
                    "sku": "DWRK-RICE-ORG-05",
                    "upc": "8901234567890",
                    "ordered_quantity": 120,
                    "unit_price": 450.00,
                    "mrp": 550.00  # matches physical label MRP 550
                }
            ],
            "delivery_pincode": "560067",
            "expected_delivery_date": "2026-06-22"
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "VERIFIED")

        # Verify DB
        po = QCPurchaseOrder.objects.get(platform_po_id="BLK-PO-2026-001")
        self.assertEqual(po.facility_code, "BLR_03")
        self.assertEqual(po.po_status, "VERIFIED")

        line = QCPOLineItem.objects.get(po=po)
        self.assertEqual(line.variant, self.variant)

    def test_blinkit_mrp_mismatch_blocks_fulfillment(self):
        """Verify MRP mismatch flags PO and blocks ASN submission."""
        # 1. Ingest PO with mismatched MRP (600 vs physical 550)
        url_webhook = reverse('quickcommerce-blinkit-po-webhook')
        payload = {
            "event": "PURCHASE_ORDER_CREATED",
            "po_id": "BLK-PO-MISMATCH",
            "vendor_id": "BLK-VND-001",
            "items": [
                {
                    "sku": "DWRK-RICE-ORG-05",
                    "upc": "8901234567890",
                    "ordered_quantity": 100,
                    "unit_price": 450.00,
                    "mrp": 600.00  # mismatch!
                }
            ],
            "delivery_pincode": "560067",
            "expected_delivery_date": "2026-06-22"
        }

        response = self.client.post(url_webhook, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "RECEIVED")
        self.assertIn("warning", response.data)

        # 2. Attempt to submit ASN -> Should be blocked (409 Conflict)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url_asn = reverse('quickcommerce-blinkit-asn-submit')
        asn_payload = {
            "po_id": "BLK-PO-MISMATCH",
            "dispatched_items": [
                {
                    "sku": "DWRK-RICE-ORG-05",
                    "dispatched_quantity": 100
                }
            ],
            "tracking_reference": "DTDC-9988"
        }

        response = self.client.post(url_asn, asn_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("error", response.data)
        self.assertIn("Fulfillment blocked due to MRP mismatch", response.data["error"])

    def test_blinkit_asn_success(self):
        """Verify successful ASN submission updates PO state and line items."""
        # 1. Ingest verified PO
        po = QCPurchaseOrder.objects.create(
            platform="blinkit",
            platform_po_id="BLK-PO-SUCCESS",
            vendor_id="BLK-VND-001",
            facility_code="BLR_03",
            po_status="VERIFIED"
        )
        line = QCPOLineItem.objects.create(
            po=po,
            variant=self.variant,
            platform_sku="DWRK-RICE-ORG-05",
            ordered_quantity=100,
            delivered_quantity=0,
            unit_price=Decimal("450.00"),
            mrp=Decimal("550.00")
        )

        # 2. Submit ASN
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url_asn = reverse('quickcommerce-blinkit-asn-submit')
        payload = {
            "po_id": "BLK-PO-SUCCESS",
            "dispatched_items": [
                {
                    "sku": "DWRK-RICE-ORG-05",
                    "dispatched_quantity": 98
                }
            ],
            "tracking_reference": "DTDC-7777"
        }

        response = self.client.post(url_asn, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ASN_SENT")
        self.assertIsNotNone(response.data["asn_reference"])

        # Check PO status and line item delivered quantity in DB
        po.refresh_from_db()
        self.assertEqual(po.po_status, "ASN_SENT")
        self.assertEqual(po.asn_reference, response.data["asn_reference"])
        
        line.refresh_from_db()
        self.assertEqual(line.delivered_quantity, 98)
