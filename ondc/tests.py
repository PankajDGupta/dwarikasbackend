import base64
import time
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.db import models

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import PublicFormat

from inventory.models import Product, ProductVariant, Reservation, Order
from ondc.beckn_auth import verify_beckn_signature


class OndcBecknIntegrationTests(TestCase):

    def setUp(self):
        # 1. Generate Ed25519 Key Pair for Signature Verification testing
        self.private_key = ed25519.Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.public_bytes = self.public_key.public_bytes_raw()
        self.public_key_b64 = base64.b64encode(self.public_bytes).decode('utf-8')

        # 2. Setup standard test data
        self.product = Product.objects.create(
            name="Handcrafted Silk Scarf",
            hsn_code="6214.10",
            gst_slab=18.00,
            product_type="apparel",
            brand="Dwarikas",
            category="Scarves"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="DW-SILK-SCARF",
            barcode="8901234567890",
            size="L",
            color="Red",
            stock_quantity=10,
            retail_price=2000.00,
            mrp=2500.00
        )

        # Common test request contexts/metadata
        self.bap_uri = "https://bap.example.com/callback"
        self.context = {
            'domain': 'nic2004:52110',
            'country': 'IND',
            'city': 'std:080',
            'action': 'search',
            'core_version': '1.2.5',
            'bap_id': 'bap.example.com',
            'bap_uri': self.bap_uri,
            'bpp_id': 'dwarikas.com',
            'bpp_uri': 'https://dwarikas.com/api/v1/ondc',
            'transaction_id': str(uuid.uuid4()),
            'message_id': str(uuid.uuid4()),
            'timestamp': '2026-06-06T12:00:00.000Z',
            'ttl': 'PT30S'
        }

    def _get_signature_headers(self, created_offset=0, expires_offset=30, custom_signature=None, custom_digest=None):
        """Helper to generate a valid Beckn Authorization signature header."""
        created = int(time.time()) + created_offset
        expires = created + expires_offset
        digest = custom_digest or "sha256-digest-hash"
        
        signing_string = f"(created): {created}\n(expires): {expires}\ndigest: {digest}"
        
        if custom_signature:
            signature_b64 = custom_signature
        else:
            sig_bytes = self.private_key.sign(signing_string.encode('utf-8'))
            signature_b64 = base64.b64encode(sig_bytes).decode('utf-8')

        auth_value = (
            f'Signature keyId="dwarikas.com|key1|ed25519",algorithm="ed25519",'
            f'created="{created}",expires="{expires}",headers="(created) (expires) digest",'
            f'signature="{signature_b64}",digest="{digest}"'
        )
        return {'HTTP_AUTHORIZATION': auth_value}

    @override_settings()
    def test_signature_verification_success(self):
        """Verify that a request with a valid Ed25519 signature is successfully verified."""
        # Configure registry public key in settings
        with self.settings(ONDC_REGISTRY_PUBLIC_KEY_B64=self.public_key_b64):
            headers = self._get_signature_headers()
            
            # Mock request object
            class DummyRequest:
                def __init__(self, headers):
                    self.headers = headers
            
            # Headers key matching django style for wsgi/rest framework
            request_headers = {'Authorization': headers['HTTP_AUTHORIZATION']}
            req = DummyRequest(request_headers)
            
            self.assertTrue(verify_beckn_signature(req))

    @override_settings()
    def test_signature_verification_failure(self):
        """Verify that an invalid signature or missing Authorization header fails verification."""
        with self.settings(ONDC_REGISTRY_PUBLIC_KEY_B64=self.public_key_b64):
            # 1. Invalid signature string
            headers = self._get_signature_headers(custom_signature="invalid-base64-sig")
            class DummyRequest:
                def __init__(self, headers):
                    self.headers = headers
            
            req = DummyRequest({'Authorization': headers['HTTP_AUTHORIZATION']})
            self.assertFalse(verify_beckn_signature(req))

            # 2. Missing headers
            req_missing = DummyRequest({})
            self.assertFalse(verify_beckn_signature(req_missing))

    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_signature_verification_bypassed_when_empty(self):
        """Verify that signature verification returns True when the key settings is empty."""
        class DummyRequest:
            def __init__(self):
                self.headers = {}
        self.assertTrue(verify_beckn_signature(DummyRequest()))

    @patch('requests.post')
    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_search_endpoint_returns_ack_and_callback(self, mock_post):
        """POST /search returns HTTP 202 ACK and dispatches on_search callback with catalog items."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Create an active reservation for 2 items to verify ATP calculations
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.uuid4(),
            reserved_quantity=2,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            status='active'
        )

        # Create an expired reservation to ensure it is ignored in ATP
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.uuid4(),
            reserved_quantity=3,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            status='active'
        )

        search_context = {**self.context, 'action': 'search'}
        payload = {
            'context': search_context,
            'message': {
                'intent': {
                    'item': {'descriptor': {'name': 'silk'}}
                }
            }
        }

        url = reverse('ondc-search')
        response = self.client.post(url, data=payload, content_type='application/json')
        
        # Verify synchronous HTTP 202 ACK response
        self.assertEqual(response.status_code, 202)
        resp_json = response.json()
        self.assertEqual(resp_json['message']['ack']['status'], 'ACK')
        self.assertEqual(resp_json['context']['action'], 'on_search')

        # Verify async callback trigger
        mock_post.assert_called_once()
        callback_url, kwargs = mock_post.call_args
        self.assertEqual(callback_url[0], f"{self.bap_uri}/on_search")
        
        # Verify the callback catalog details
        callback_data = kwargs['json']
        self.assertEqual(callback_data['context']['action'], 'on_search')
        
        items = callback_data['message']['catalog']['bpp/providers'][0]['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['id'], str(self.variant.id))
        self.assertEqual(items[0]['descriptor']['code'], self.variant.sku)
        self.assertEqual(items[0]['price']['value'], "2000.00")
        
        # ATP calculation check: Physical (10) - Active Res (2) = 8
        self.assertEqual(items[0]['quantity']['available']['count'], 8)

    @patch('requests.post')
    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_select_endpoint_creates_reservation_and_returns_quote(self, mock_post):
        """POST /select creates a Reservation stock hold and returns quote breakup in callback."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        select_context = {**self.context, 'action': 'select'}
        payload = {
            'context': select_context,
            'message': {
                'order': {
                    'items': [
                        {
                            'id': str(self.variant.id),
                            'quantity': {'count': 3}
                        }
                    ]
                }
            }
        }

        url = reverse('ondc-select')
        response = self.client.post(url, data=payload, content_type='application/json')
        self.assertEqual(response.status_code, 202)

        # Verify Reservation is created in DB
        res_qs = Reservation.objects.filter(variant=self.variant, status='active')
        self.assertEqual(res_qs.count(), 1)
        reservation = res_qs.first()
        self.assertEqual(reservation.reserved_quantity, 3)
        self.assertTrue(reservation.expires_at > datetime.now(timezone.utc) + timedelta(minutes=9))

        # Verify BAP callback details
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        callback_data = kwargs['json']
        self.assertEqual(callback_data['context']['action'], 'on_select')
        self.assertNotIn('error', callback_data)

        order_data = callback_data['message']['order']
        self.assertEqual(order_data['items'][0]['fulfillment_id'], str(reservation.id))
        
        # Price and Quote checks
        # Price of 3 items = 2000 * 3 = 6000. GST = 18% of 6000 = 1080. Total = 7080.
        self.assertEqual(order_data['quote']['price']['value'], "7080.00")
        self.assertEqual(len(order_data['quote']['breakup']), 1)
        self.assertEqual(order_data['quote']['breakup'][0]['price']['value'], "7080.00")
        self.assertEqual(order_data['quote']['breakup'][0]['tax']['value'], "1080.00")

    @patch('requests.post')
    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_select_insufficient_stock(self, mock_post):
        """POST /select fails to reserve items if requested quantity exceeds ATP."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        select_context = {**self.context, 'action': 'select'}
        payload = {
            'context': select_context,
            'message': {
                'order': {
                    'items': [
                        {
                            'id': str(self.variant.id),
                            'quantity': {'count': 15}  # Exceeds physical stock of 10
                        }
                    ]
                }
            }
        }

        url = reverse('ondc-select')
        response = self.client.post(url, data=payload, content_type='application/json')
        self.assertEqual(response.status_code, 202)

        # Assert no reservation was created
        self.assertEqual(Reservation.objects.filter(variant=self.variant, status='active').count(), 0)

        # Assert callback details returned Out Of Stock error
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        callback_data = kwargs['json']
        self.assertEqual(callback_data['context']['action'], 'on_select')
        self.assertEqual(callback_data['error']['code'], '40002')
        self.assertIn('out of stock', callback_data['error']['message'].lower())

    @patch('requests.post')
    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_init_endpoint_splits_gst_correctly(self, mock_post):
        """POST /init returns tax breakup routed via CGST/SGST (intrastate) or IGST (interstate)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Setup an active reservation hold
        res = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.uuid4(),
            reserved_quantity=2,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            status='active'
        )

        init_context = {**self.context, 'action': 'init'}
        
        # Intrastate billing state (same state e.g., Karnataka)
        payload_local = {
            'context': init_context,
            'message': {
                'order': {
                    'items': [
                        {
                            'id': str(self.variant.id),
                            'quantity': {'count': 2},
                            'fulfillment_id': str(res.id)
                        }
                    ],
                    'billing': {
                        'name': 'Pankaj Gupta',
                        'address': {'state': 'Karnataka', 'city': 'Bengaluru'}
                    }
                }
            }
        }

        url = reverse('ondc-init')
        response = self.client.post(url, data=payload_local, content_type='application/json')
        self.assertEqual(response.status_code, 202)

        # Verify local GST routing (CGST / SGST split)
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        callback_data = kwargs['json']
        self.assertEqual(callback_data['context']['action'], 'on_init')
        
        # Total price subtotal = 2000 * 2 = 4000. GST 18% = 720. CGST = 360, SGST = 360.
        breakup = callback_data['message']['order']['quote']['breakup'][0]
        self.assertEqual(breakup['price']['value'], "4000.00")
        
        taxes = {t['title']: t['price']['value'] for t in breakup['tax']}
        self.assertIn('CGST', taxes)
        self.assertIn('SGST', taxes)
        self.assertEqual(taxes['CGST'], "360.00")
        self.assertEqual(taxes['SGST'], "360.00")

        # Now test interstate routing (different state e.g., Maharashtra)
        mock_post.reset_mock()
        payload_interstate = {
            'context': init_context,
            'message': {
                'order': {
                    'items': [
                        {
                            'id': str(self.variant.id),
                            'quantity': {'count': 2},
                            'fulfillment_id': str(res.id)
                        }
                    ],
                    'billing': {
                        'name': 'Pankaj Gupta',
                        'address': {'state': 'Maharashtra', 'city': 'Mumbai'}
                    }
                }
            }
        }

        response = self.client.post(url, data=payload_interstate, content_type='application/json')
        self.assertEqual(response.status_code, 202)

        # Verify interstate GST routing (IGST full amount)
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        callback_data = kwargs['json']
        
        breakup = callback_data['message']['order']['quote']['breakup'][0]
        taxes = {t['title']: t['price']['value'] for t in breakup['tax']}
        self.assertIn('IGST', taxes)
        self.assertEqual(taxes['IGST'], "720.00")

    @patch('requests.post')
    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_confirm_endpoint_decrements_stock_and_creates_order(self, mock_post):
        """POST /confirm processes final payment, decrements stock atomically, and logs order."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Setup an active reservation hold
        res = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.uuid4(),
            reserved_quantity=3,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            status='active'
        )

        confirm_context = {**self.context, 'action': 'confirm'}
        payload = {
            'context': confirm_context,
            'message': {
                'order': {
                    'items': [
                        {
                            'id': str(self.variant.id),
                            'quantity': {'count': 3},
                            'fulfillment_id': str(res.id)
                        }
                    ],
                    'billing': {'name': 'Pankaj Gupta'},
                    'payment': {'uri': 'https://upi.example.com', 'params': {'transaction_id': 'tx123'}}
                }
            }
        }

        url = reverse('ondc-confirm')
        response = self.client.post(url, data=payload, content_type='application/json')
        self.assertEqual(response.status_code, 202)

        # Verify DB changes: stock decremented
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 7)  # 10 - 3 = 7

        # Verify Reservation is set completed
        res.refresh_from_db()
        self.assertEqual(res.status, 'completed')

        # Verify Order logged in database
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.first()
        self.assertEqual(order.payment_status, 'completed')
        # Subtotal: 6000 + 1080 GST = 7080
        self.assertEqual(float(order.total_amount), 7080.00)

        # Verify Callback output
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        callback_data = kwargs['json']
        self.assertEqual(callback_data['context']['action'], 'on_confirm')
        self.assertEqual(callback_data['message']['order']['id'], str(order.id))
        self.assertEqual(callback_data['message']['order']['state'], 'Accepted')
        self.assertEqual(callback_data['message']['order']['payment']['status'], 'PAID')

    @patch('requests.post')
    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_status_endpoint_returns_db_order_details(self, mock_post):
        """POST /status returns details and status coordinates of a logged order."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Log order manually in DB
        db_order = Order.objects.create(
            user_id=uuid.uuid4(),
            total_amount=4720.00,
            gst_amount=720.00,
            payment_method='UPI',
            payment_status='completed'
        )

        status_context = {**self.context, 'action': 'status'}
        payload = {
            'context': status_context,
            'message': {
                'order_id': str(db_order.id)
            }
        }

        url = reverse('ondc-status')
        response = self.client.post(url, data=payload, content_type='application/json')
        self.assertEqual(response.status_code, 202)

        # Verify callback content
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        callback_data = kwargs['json']
        self.assertEqual(callback_data['context']['action'], 'on_status')
        self.assertEqual(callback_data['message']['order']['id'], str(db_order.id))
        self.assertEqual(callback_data['message']['order']['state'], 'Accepted')
        self.assertEqual(callback_data['message']['order']['quote']['price']['value'], "4720.00")

    @patch('requests.post')
    @override_settings(ONDC_REGISTRY_PUBLIC_KEY_B64="")
    def test_cancel_endpoint_releases_reservation(self, mock_post):
        """POST /cancel releases active reservation hold and sets it expired."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Setup an active reservation hold
        res = Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.uuid4(),
            reserved_quantity=2,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            status='active'
        )

        cancel_context = {**self.context, 'action': 'cancel'}
        payload = {
            'context': cancel_context,
            'message': {
                'fulfillment_id': str(res.id)
            }
        }

        url = reverse('ondc-cancel')
        response = self.client.post(url, data=payload, content_type='application/json')
        self.assertEqual(response.status_code, 202)

        # Verify Reservation is now marked expired (released hold)
        res.refresh_from_db()
        self.assertEqual(res.status, 'expired')

        # Verify callback content
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        callback_data = kwargs['json']
        self.assertEqual(callback_data['context']['action'], 'on_cancel')
        self.assertEqual(callback_data['message']['order']['id'], str(res.id))
        self.assertEqual(callback_data['message']['order']['status'], 'Cancelled')
