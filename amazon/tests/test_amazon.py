import jwt
import uuid
import json
from decimal import Decimal
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch, MagicMock

from inventory.models import Product, ProductVariant
from amazon.models import AmazonCredentials, AmazonListing
from amazon.sns_verifier import verify_sns_signature

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
class AmazonIntegrationTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        cache.clear()

        # Seed test credentials
        self.credentials = AmazonCredentials.objects.create(
            seller_id="A3ABCDEFOOBAR",
            lwa_client_id="amzn1.application-oa2-client.test",
            lwa_client_secret="testsecret",
            lwa_refresh_token="testrefreshtoken",
            region="EU",
            primary_marketplace_id="A21TJRUUN4KGV"
        )

        # Seed test product & variant
        self.product = Product.objects.create(
            name="Premium Basmati Rice",
            hsn_code="1006",
            gst_slab=Decimal("5.00"),
            brand="Dwarika's Farms",
            category="Groceries",
            description="Pure organic long grain rice."
        )

        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DWRK-RICE-ORG-05",
            barcode="8901234567890",
            stock_quantity=100,
            retail_price=Decimal("499.00"),
            mrp=Decimal("550.00")
        )

        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

    def test_sync_endpoint_rbac(self):
        """Verify only staff/managers can trigger SP-API sync."""
        url = reverse('amazon-listings-sync')
        payload = {
            "product_id": str(self.product.id),
            "marketplace_id": "A21TJRUUN4KGV"
        }

        # 1. Anonymous request -> 401
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Staff -> 202 (mocked backend)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        with patch('amazon.views.put_listings_item') as mock_put:
            mock_put.return_value = {
                "status": "ACCEPTED",
                "submissionId": "7df4e528-9844-46ab-8991-6cf1b54c86e2",
                "issues": []
            }
            response = self.client.post(url, payload)
            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_sync_invalid_product_id(self):
        """Verify validation failure for nonexistent product ID."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url = reverse('amazon-listings-sync')
        payload = {
            "product_id": str(uuid.uuid4()),
            "marketplace_id": "A21TJRUUN4KGV"
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product_id", response.data)

    def test_sync_product_without_barcode(self):
        """Verify validation failure when product variants have no barcode."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url = reverse('amazon-listings-sync')
        
        product_no_barcode = Product.objects.create(
            name="No Barcode Product",
            hsn_code="1111",
            gst_slab=Decimal("12.00")
        )
        ProductVariant.objects.create(
            product=product_no_barcode,
            sku="DW-NO-BARCODE",
            stock_quantity=10,
            retail_price=Decimal("100.00")
        )

        payload = {
            "product_id": str(product_no_barcode.id),
            "marketplace_id": "A21TJRUUN4KGV"
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product_id", response.data)
        self.assertEqual(response.data["product_id"][0], "Product has no EAN/UPC barcode.")

    @patch('amazon.views.put_listings_item')
    @patch('amazon.lwa_client.requests.post')
    def test_sync_success_accepted(self, mock_lwa, mock_put):
        """Verify successful putListingsItem call updates state and returns 202."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url = reverse('amazon-listings-sync')
        payload = {
            "product_id": str(self.product.id),
            "marketplace_id": "A21TJRUUN4KGV"
        }

        # Mock LWA Token response
        mock_lwa_resp = MagicMock()
        mock_lwa_resp.json.return_value = {
            "access_token": "Atzr|mock_access_token",
            "expires_in": 3600
        }
        mock_lwa_resp.status_code = 200
        mock_lwa.return_value = mock_lwa_resp

        # Mock SP-API put response
        mock_put.return_value = {
            "status": "ACCEPTED",
            "submissionId": "7df4e528-9844-46ab-8991-6cf1b54c86e2",
            "issues": []
        }

        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], "SUBMITTED")
        self.assertEqual(response.data["submission_id"], "7df4e528-9844-46ab-8991-6cf1b54c86e2")

        # Verify DB listing row
        listing = AmazonListing.objects.get(sku=self.variant.sku)
        self.assertEqual(listing.sync_status, "SUBMITTED")
        self.assertEqual(str(listing.submission_id), "7df4e528-9844-46ab-8991-6cf1b54c86e2")
        self.assertEqual(listing.price_synced, self.variant.retail_price)

    @patch('amazon.views.put_listings_item')
    def test_sync_validation_failure_invalid(self, mock_put):
        """Verify SP-API immediate rejection updates state to INVALID and logs issues."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url = reverse('amazon-listings-sync')
        payload = {
            "product_id": str(self.product.id),
            "marketplace_id": "A21TJRUUN4KGV"
        }

        # Mock validation issue rejection
        mock_put.return_value = {
            "status": "INVALID",
            "submissionId": "7df4e528-9844-46ab-8991-6cf1b54c86e2",
            "issues": [
                {
                    "code": "90220",
                    "message": "The field 'externally_assigned_product_identifier' format is invalid for type 'ean'.",
                    "severity": "ERROR",
                    "attributeNames": ["externally_assigned_product_identifier"]
                }
            ]
        }

        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data["status"], "INVALID")
        self.assertEqual(len(response.data["issues"]), 1)

        # Verify database
        listing = AmazonListing.objects.get(sku=self.variant.sku)
        self.assertEqual(listing.sync_status, "INVALID")
        self.assertEqual(len(listing.validation_issues), 1)
        self.assertEqual(listing.validation_issues[0]["code"], "90220")

    def test_sync_idempotency(self):
        """Verify sync endpoint immediately returns existing active/submitted listing status."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        url = reverse('amazon-listings-sync')

        # Pre-seed active listing
        AmazonListing.objects.create(
            product=self.product,
            sku=self.variant.sku,
            marketplace_id="A21TJRUUN4KGV",
            sync_status="ACTIVE",
            asin="B01NXYZ123"
        )

        payload = {
            "product_id": str(self.product.id),
            "marketplace_id": "A21TJRUUN4KGV"
        }

        with patch('amazon.views.put_listings_item') as mock_put:
            response = self.client.post(url, payload)
            # Should return 200 OK immediately without calling SP-API
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["status"], "ACTIVE")
            mock_put.assert_not_called()

    def test_status_polling_endpoint(self):
        """Verify listing status check returns listing states and validation issues."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        
        # 1. Product not tracked -> 404
        url_not_found = reverse('amazon-listings-status', kwargs={"product_id": str(uuid.uuid4())})
        response = self.client.get(url_not_found)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 2. Product tracked -> 200 OK
        AmazonListing.objects.create(
            product=self.product,
            sku=self.variant.sku,
            marketplace_id="A21TJRUUN4KGV",
            sync_status="ACTIVE",
            asin="B01NXYZ123",
            validation_issues=[]
        )
        url_success = reverse('amazon-listings-status', kwargs={"product_id": str(self.product.id)})
        response = self.client.get(url_success)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sync_status"], "ACTIVE")
        self.assertEqual(response.data["asin"], "B01NXYZ123")
        self.assertEqual(response.data["issues"], [])

    @patch('amazon.views.requests.get')
    def test_webhook_sns_subscription_confirmation(self, mock_get):
        """Verify receiver handles SNS subscription confirmation calls."""
        url = reverse('amazon-sqs-webhook')
        payload = {
            "Type": "SubscriptionConfirmation",
            "MessageId": "11111111-2222-3333-4444-555555555555",
            "Token": "test_token",
            "TopicArn": "arn:aws:sns:eu-west-1:123456789012:DwarikasAmazonListings",
            "Message": "Subscription Confirmation details",
            "SubscribeURL": "https://sns.eu-west-1.amazonaws.com/?Action=ConfirmSubscription&TopicArn=arn:aws:sns:eu-west-1:123456789012:DwarikasAmazonListings",
            "MockVerification": "True"
        }

        mock_get.return_value.status_code = 200
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_get.assert_called_once_with("https://sns.eu-west-1.amazonaws.com/?Action=ConfirmSubscription&TopicArn=arn:aws:sns:eu-west-1:123456789012:DwarikasAmazonListings", timeout=10)

    def test_webhook_status_change_buyable(self):
        """Verify webhook processes LISTINGS_ITEM_STATUS_CHANGE BUYABLE event and updates status to ACTIVE."""
        url = reverse('amazon-sqs-webhook')
        
        # Pre-seed listing in SUBMITTED state
        listing = AmazonListing.objects.create(
            product=self.product,
            sku=self.variant.sku,
            marketplace_id="A21TJRUUN4KGV",
            sync_status="SUBMITTED"
        )

        message_payload = {
            "notificationType": "LISTINGS_ITEM_STATUS_CHANGE",
            "payload": {
                "sellerId": "A3ABCDEFOOBAR",
                "sku": self.variant.sku,
                "marketplaceId": "A21TJRUUN4KGV",
                "status": "BUYABLE",
                "asin": "B01NXYZ123"
            }
        }

        payload = {
            "Type": "Notification",
            "MessageId": "8c35e679-b1d6-512c-91bc-0e19488a0b0f",
            "TopicArn": "arn:aws:sns:eu-west-1:123456789012:DwarikasAmazonListings",
            "Message": json.dumps(message_payload),
            "MockVerification": "True"
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify DB update
        listing.refresh_from_db()
        self.assertEqual(listing.sync_status, "ACTIVE")
        self.assertEqual(listing.asin, "B01NXYZ123")

    def test_webhook_status_change_suppressed(self):
        """Verify webhook processes SUPPRESSED event and updates status to SUPPRESSED."""
        url = reverse('amazon-sqs-webhook')
        
        listing = AmazonListing.objects.create(
            product=self.product,
            sku=self.variant.sku,
            marketplace_id="A21TJRUUN4KGV",
            sync_status="ACTIVE",
            asin="B01NXYZ123"
        )

        message_payload = {
            "notificationType": "LISTINGS_ITEM_STATUS_CHANGE",
            "payload": {
                "sellerId": "A3ABCDEFOOBAR",
                "sku": self.variant.sku,
                "marketplaceId": "A21TJRUUN4KGV",
                "status": "SUPPRESSED",
                "asin": "B01NXYZ123"
            }
        }

        payload = {
            "Type": "Notification",
            "MessageId": "8c35e679-b1d6-512c-91bc-0e19488a0b0f",
            "TopicArn": "arn:aws:sns:eu-west-1:123456789012:DwarikasAmazonListings",
            "Message": json.dumps(message_payload),
            "MockVerification": "True"
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        listing.refresh_from_db()
        self.assertEqual(listing.sync_status, "SUPPRESSED")

    def test_webhook_issues_change_error(self):
        """Verify webhook processes LISTINGS_ITEM_ISSUES_CHANGE event containing severity ERROR."""
        url = reverse('amazon-sqs-webhook')
        
        listing = AmazonListing.objects.create(
            product=self.product,
            sku=self.variant.sku,
            marketplace_id="A21TJRUUN4KGV",
            sync_status="ACTIVE",
            asin="B01NXYZ123"
        )

        issues = [
            {
                "code": "8560",
                "message": "Missing SKU attributes.",
                "severity": "ERROR"
            }
        ]

        message_payload = {
            "notificationType": "LISTINGS_ITEM_ISSUES_CHANGE",
            "payload": {
                "sellerId": "A3ABCDEFOOBAR",
                "sku": self.variant.sku,
                "marketplaceId": "A21TJRUUN4KGV",
                "issues": issues
            }
        }

        payload = {
            "Type": "Notification",
            "MessageId": "8c35e679-b1d6-512c-91bc-0e19488a0b0f",
            "TopicArn": "arn:aws:sns:eu-west-1:123456789012:DwarikasAmazonListings",
            "Message": json.dumps(message_payload),
            "MockVerification": "True"
        }

        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        listing.refresh_from_db()
        self.assertEqual(listing.sync_status, "ERROR")
        self.assertEqual(len(listing.validation_issues), 1)
        self.assertEqual(listing.validation_issues[0]["code"], "8560")
