import jwt
from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch, MagicMock

from inventory.models import Product, ProductVariant, Order
from quickcommerce.models import QCPlatformListing, QCPlatformCredentials, QCWarehouseMapping

def generate_test_jwt(role, user_id="00000000-0000-0000-0000-000000000001", email="staff@example.com"):
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


@override_settings(
    SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long",
    IS_TESTING=True
)
class JioMartIntegrationTests(TestCase):

    def setUp(self):
        self.client = APIClient()

        # Seed JioMart credentials
        QCPlatformCredentials.objects.create(
            platform='jiomart',
            fynd_username="dwarika_test_user",
            fynd_access_token="testaccesstoken",
            fynd_token_expires_at=timezone_now_future()
        )

        # Seed warehouse mapping
        QCWarehouseMapping.objects.create(
            platform='jiomart',
            internal_facility_code='UCFacilityCode1',
            platform_location_id='jiomartLocationId1',
            mapping_type='facility'
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

    def test_sync_trigger_rbac(self):
        """Verify only staff/managers can trigger JioMart listing sync."""
        url = reverse('quickcommerce-listings-sync')
        payload = {
            "product_id": str(self.product.id),
            "platforms": ["jiomart"],
            "fssai_license": "10012345000001",
            "marketplace_config": {
                "jiomart": {
                    "location_ids": ["jiomartLocationId1"]
                }
            }
        }

        # 1. Anonymous request -> 401
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff -> 202
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["success"], True)
        self.assertEqual(response.data["results"]["jiomart"]["status"], "SUBMITTED")

    def test_batch_submission_updates_db(self):
        """Verify successful batch sync creates listing record in SUBMITTED state."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url = reverse('quickcommerce-listings-sync')
        payload = {
            "product_id": str(self.product.id),
            "platforms": ["jiomart"],
            "fssai_license": "10012345000001",
            "marketplace_config": {
                "jiomart": {
                    "location_ids": ["jiomartLocationId1"]
                }
            }
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        # Check listing record in DB
        listing = QCPlatformListing.objects.get(product=self.product, platform='jiomart')
        self.assertEqual(listing.sync_status, "SUBMITTED")
        self.assertIsNotNone(listing.trace_id)
        self.assertEqual(listing.mrp_snapshot, self.variant.mrp)

    def test_jiomart_batch_polling_success(self):
        """Verify internal task poller transitions status to ACTIVE on COMPLETED."""
        listing = QCPlatformListing.objects.create(
            product=self.product,
            variant=self.variant,
            platform='jiomart',
            trace_id="test-trace-id",
            sync_status="SUBMITTED"
        )

        url = reverse('quickcommerce-task-poll-jiomart')
        payload = {
            "trace_id": "test-trace-id",
            "retry_count": 1  # forces mock to return COMPLETED directly
        }

        # Simulated cloud tasks dispatch requires no auth headers
        headers = {'HTTP_X_CLOUDTASKS_TASKNAME': 'local-test-task'}
        response = self.client.post(url, payload, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        listing.refresh_from_db()
        self.assertEqual(listing.sync_status, "ACTIVE")
        self.assertEqual(listing.validation_issues, [])

    def test_jiomart_order_webhook_flow(self):
        """Verify JioMart order webhook maps orders to WMS facilities."""
        url = reverse('quickcommerce-jiomart-order-webhook')
        order_uuid = "00000000-0000-0000-0000-000000000099"
        payload = {
            "order_id": order_uuid,
            "location_id": "jiomartLocationId1",
            "total_amount": 1200.00
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["facility_code"], "UCFacilityCode1")
        self.assertEqual(response.data["status"], "staged")

        # Verify order in WMS database
        order = Order.objects.get(id=order_uuid)
        self.assertEqual(order.carrier_status, "staged")

    def test_jiomart_manifest_closure(self):
        """Verify manifest closure updates carrier_status to picked_up."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        order_uuid = "00000000-0000-0000-0000-000000000099"
        Order.objects.create(
            id=order_uuid,
            total_amount=Decimal("1200.00"),
            gst_amount=Decimal("120.00"),
            payment_method="online",
            payment_status="completed",
            carrier_status="staged"
        )

        url = reverse('quickcommerce-jiomart-manifest-close')
        payload = {
            "jiomart_order_id": order_uuid,
            "manifest_id": "MFT-987654"
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["carrier_status"], "picked_up")
        self.assertEqual(response.data["tracking_reference"], "MFT-987654")

        # Verify DB
        order = Order.objects.get(id=order_uuid)
        self.assertEqual(order.carrier_status, "picked_up")


def timezone_now_future():
    from django.utils import timezone
    import datetime
    return timezone.now() + datetime.timedelta(hours=1)
