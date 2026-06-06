import hashlib
import hmac
import time
import os
from io import StringIO
from unittest.mock import patch
import uuid
import jwt

from django.test import TestCase, override_settings
from django.core.management import call_command
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant, Order, ExternalApiKey
from inventory.tests.test_views import generate_test_jwt


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class ExternalPartnerAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.signing_secret = "test-signing-secret"
        os.environ['EXTERNAL_SIGNING_SECRET'] = self.signing_secret

        # Create base test product and variant
        self.product = Product.objects.create(
            name="Test Silk Scarf",
            hsn_code="6214",
            gst_slab=18.00,
            brand="Dwarikas",
            category="Apparel"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DWA-SCARF-RED",
            barcode="8901234567890",
            stock_quantity=50,
            retail_price=1500.00,
            unit_of_measure="pcs"
        )

        # Create base test order
        self.order = Order.objects.create(
            total_amount=1770.00,
            gst_amount=270.00,
            payment_method="UPI",
            payment_status="completed"
        )

        # Generate standard key for testing M2M auth
        self.raw_key = "test_raw_partner_api_key_value_string"
        self.key_hash = hashlib.sha256(self.raw_key.encode()).hexdigest()
        self.api_key = ExternalApiKey.objects.create(
            partner_name="ERP Test System",
            key_hash=self.key_hash,
            is_active=True
        )

        # JWT tokens for admin endpoint testing
        self.manager_token = generate_test_jwt("manager")
        self.staff_token = generate_test_jwt("staff")
        self.customer_token = generate_test_jwt("customer")

    def tearDown(self):
        if 'EXTERNAL_SIGNING_SECRET' in os.environ:
            del os.environ['EXTERNAL_SIGNING_SECRET']

    def _get_signed_headers(self, method, path, timestamp, body_str, raw_key):
        """Helper to generate signed headers for partner requests."""
        payload_str = method + path + str(timestamp) + body_str
        signature = hmac.new(self.signing_secret.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
        return {
            'HTTP_X_DWARIKAS_API_KEY': raw_key,
            'HTTP_X_DWARIKAS_SIGNATURE': signature,
            'HTTP_X_DWARIKAS_TIMESTAMP': str(timestamp)
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Management Command Tests
    # ──────────────────────────────────────────────────────────────────────────
    def test_management_command_creates_key(self):
        """Management command create_api_key successfully registers partner key."""
        out = StringIO()
        call_command('create_api_key', partner_name='CLI Test Partner', stdout=out)
        output = out.getvalue()

        self.assertIn('API key created for partner: CLI Test Partner', output)
        self.assertIn('Copy this key now', output)

        # Retrieve the key from database
        db_key = ExternalApiKey.objects.get(partner_name='CLI Test Partner')
        self.assertTrue(db_key.is_active)

        # Extract the raw key from CLI output and verify hash matching
        raw_key_line = [line.strip() for line in output.split('\n') if line.strip()][-1]
        expected_hash = hashlib.sha256(raw_key_line.encode()).hexdigest()
        self.assertEqual(db_key.key_hash, expected_hash)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Authentication & Request Signature Security Tests
    # ──────────────────────────────────────────────────────────────────────────
    def test_auth_missing_api_key_header(self):
        """Request without X-Dwarikas-Api-Key returns 401."""
        url = reverse('external-inventory-sync')
        response = self.client.post(url, data={}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_auth_invalid_api_key(self):
        """Request with invalid X-Dwarikas-Api-Key returns 401."""
        url = reverse('external-inventory-sync')
        headers = self._get_signed_headers('POST', url, int(time.time()), '{}', 'wrong_api_key')
        response = self.client.post(url, data={}, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_auth_revoked_api_key(self):
        """Request with revoked is_active=False API key returns 401."""
        self.api_key.is_active = False
        self.api_key.save()

        url = reverse('external-inventory-sync')
        headers = self._get_signed_headers('POST', url, int(time.time()), '{}', self.raw_key)
        response = self.client.post(url, data={}, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_auth_missing_signature_or_timestamp(self):
        """Request with API key but missing signature or timestamp returns 401."""
        url = reverse('external-inventory-sync')

        # 1. Missing signature
        headers = {
            'HTTP_X_DWARIKAS_API_KEY': self.raw_key,
            'HTTP_X_DWARIKAS_TIMESTAMP': str(int(time.time()))
        }
        response = self.client.post(url, data={}, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Missing timestamp
        headers = {
            'HTTP_X_DWARIKAS_API_KEY': self.raw_key,
            'HTTP_X_DWARIKAS_SIGNATURE': 'fake_signature'
        }
        response = self.client.post(url, data={}, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_auth_expired_timestamp(self):
        """Request with timestamp > 300 seconds old returns 401."""
        url = reverse('external-inventory-sync')
        expired_timestamp = int(time.time()) - 301

        headers = self._get_signed_headers('POST', url, expired_timestamp, '{}', self.raw_key)
        response = self.client.post(url, data={}, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_auth_invalid_signature(self):
        """Request with invalid signature returns 401."""
        url = reverse('external-inventory-sync')
        timestamp = int(time.time())

        headers = self._get_signed_headers('POST', url, timestamp, '{}', self.raw_key)
        headers['HTTP_X_DWARIKAS_SIGNATURE'] = 'incorrect_signature_hash'
        response = self.client.post(url, data={}, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Admin API Key Management Endpoint Tests
    # ──────────────────────────────────────────────────────────────────────────
    def test_admin_list_keys_permissions(self):
        """Verify role permissions on API Key listing."""
        url = reverse('admin-api-key-list-create')

        # 1. Anonymous -> 401
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
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['partner_name'], 'ERP Test System')
        self.assertNotIn('key_hash', response.data[0])

    def test_admin_create_key_permissions(self):
        """Verify role permissions on API Key generation."""
        url = reverse('admin-api-key-list-create')
        payload = {'partner_name': 'New ERP Partner'}

        # 1. Anonymous -> 401
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Manager -> 201 Created
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['partner_name'], 'New ERP Partner')
        self.assertIn('raw_key', response.data)

        # Verify key hash exists in DB
        db_key = ExternalApiKey.objects.get(partner_name='New ERP Partner')
        expected_hash = hashlib.sha256(response.data['raw_key'].encode()).hexdigest()
        self.assertEqual(db_key.key_hash, expected_hash)

    def test_admin_revoke_key_permissions(self):
        """Verify role permissions on API Key revocation."""
        url = reverse('admin-api-key-revoke', kwargs={'pk': self.api_key.id})

        # 1. Anonymous -> 401
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Staff -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Manager -> 200 OK
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.api_key.refresh_from_db()
        self.assertFalse(self.api_key.is_active)

    # ──────────────────────────────────────────────────────────────────────────
    # 4. External Integration Endpoint Tests
    # ──────────────────────────────────────────────────────────────────────────
    def test_inventory_sync_success(self):
        """Reconciliation POST applies quantity_delta changes successfully."""
        url = reverse('external-inventory-sync')
        timestamp = int(time.time())
        payload = {
            'sync_items': [
                {'sku': 'DWA-SCARF-RED', 'quantity_delta': 15},
                {'sku': 'DWA-SCARF-RED', 'quantity_delta': -5}
            ]
        }
        import json
        body_str = json.dumps(payload, separators=(',', ':'))

        headers = self._get_signed_headers('POST', url, timestamp, body_str, self.raw_key)
        response = self.client.post(url, data=payload, format='json', **headers)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['applied']), 2)
        self.assertEqual(len(response.data['not_found']), 0)
        self.assertEqual(len(response.data['errors']), 0)

        # Verify stock updated in database (50 + 15 - 5 = 60)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 60)

    def test_inventory_sync_missing_items_or_malformed(self):
        """Sync returns errors for non-existent SKUs and malformed quantity_delta."""
        url = reverse('external-inventory-sync')
        timestamp = int(time.time())
        payload = {
            'sync_items': [
                {'sku': 'NON-EXISTENT-SKU', 'quantity_delta': 10},
                {'sku': 'DWA-SCARF-RED', 'quantity_delta': 'invalid_delta'},
                {'sku': 'DWA-SCARF-RED'}  # Missing quantity_delta
            ]
        }
        import json
        body_str = json.dumps(payload, separators=(',', ':'))

        headers = self._get_signed_headers('POST', url, timestamp, body_str, self.raw_key)
        response = self.client.post(url, data=payload, format='json', **headers)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['applied']), 0)
        self.assertEqual(response.data['not_found'], ['NON-EXISTENT-SKU'])
        self.assertEqual(len(response.data['errors']), 2)

    def test_shipment_update_success(self):
        """Webhook shipment status patch updates Order details successfully."""
        url = reverse('external-shipment-update')
        timestamp = int(time.time())
        payload = {
            'order_id': str(self.order.id),
            'carrier_status': 'in_transit',
            'tracking_reference': 'TRACK-12345'
        }
        import json
        body_str = json.dumps(payload, separators=(',', ':'))

        headers = self._get_signed_headers('PATCH', url, timestamp, body_str, self.raw_key)
        response = self.client.patch(url, data=payload, format='json', **headers)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['order_id'], str(self.order.id))
        self.assertEqual(response.data['carrier_status'], 'in_transit')

        # Verify fields in database
        self.order.refresh_from_db()
        self.assertEqual(self.order.carrier_status, 'in_transit')
        self.assertEqual(self.order.tracking_reference, 'TRACK-12345')

    def test_shipment_update_validation_errors(self):
        """Webhook shipment status validation rejects missing/invalid attributes."""
        url = reverse('external-shipment-update')
        timestamp = int(time.time())

        # 1. Missing tracking reference
        payload = {
            'order_id': str(self.order.id),
            'carrier_status': 'in_transit'
        }
        import json
        body_str = json.dumps(payload, separators=(',', ':'))
        headers = self._get_signed_headers('PATCH', url, timestamp, body_str, self.raw_key)
        response = self.client.patch(url, data=payload, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 2. Invalid carrier status
        payload = {
            'order_id': str(self.order.id),
            'carrier_status': 'invalid_status_enum',
            'tracking_reference': 'TRACK-999'
        }
        body_str = json.dumps(payload, separators=(',', ':'))
        headers = self._get_signed_headers('PATCH', url, timestamp, body_str, self.raw_key)
        response = self.client.patch(url, data=payload, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. Non-existent Order ID
        fake_uuid = str(uuid.uuid4())
        payload = {
            'order_id': fake_uuid,
            'carrier_status': 'delivered',
            'tracking_reference': 'TRACK-999'
        }
        body_str = json.dumps(payload, separators=(',', ':'))
        headers = self._get_signed_headers('PATCH', url, timestamp, body_str, self.raw_key)
        response = self.client.patch(url, data=payload, format='json', **headers)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
