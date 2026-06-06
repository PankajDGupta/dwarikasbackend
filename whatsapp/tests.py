import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from inventory.models import Product, ProductVariant, Reservation
from whatsapp.intent_parser import classify_intent, extract_sku_from_message
from whatsapp.handlers import (
    handle_catalog, handle_stock_check,
    handle_order_status, handle_fallback
)


class WhatsAppIntentParserTests(TestCase):

    def test_classify_intent(self):
        self.assertEqual(classify_intent("catalog"), "catalog")
        self.assertEqual(classify_intent("Please show products"), "catalog")
        self.assertEqual(classify_intent("check stock"), "stock_check")
        self.assertEqual(classify_intent("Is SKU-123 in stock?"), "stock_check")
        self.assertEqual(classify_intent("track my order"), "order_status")
        self.assertEqual(classify_intent("what is the status"), "order_status")
        self.assertEqual(classify_intent("hello"), "fallback")

    def test_extract_sku_from_message(self):
        self.assertEqual(extract_sku_from_message("check stock HS-102"), "HS-102")
        self.assertEqual(extract_sku_from_message("is SKU123 available?"), "SKU123")
        self.assertEqual(extract_sku_from_message("hello standard"), "STANDARD")
        self.assertEqual(extract_sku_from_message("hi"), None)


class WhatsAppHandlersTests(TestCase):

    def setUp(self):
        # Create a test product and variants
        self.product = Product.objects.create(
            name="Silk Scarf",
            hsn_code="6214.10.10",
            gst_slab=18.00
        )
        self.variant1 = ProductVariant.objects.create(
            product=self.product,
            sku="SILK-SCARF-RED",
            stock_quantity=10,
            retail_price=999.00
        )
        self.variant2 = ProductVariant.objects.create(
            product=self.product,
            sku="SILK-SCARF-BLU",
            stock_quantity=5,
            retail_price=999.00
        )

    @patch('whatsapp.handlers.send_list_message')
    def test_handle_catalog(self, mock_send_list):
        handle_catalog("12345")
        mock_send_list.assert_called_once()
        args, kwargs = mock_send_list.call_args
        self.assertEqual(kwargs['to'], "12345")
        self.assertEqual(kwargs['header_text'], "Dwarikas Catalogue")
        self.assertEqual(len(kwargs['sections'][0]['rows']), 2)

    @patch('whatsapp.handlers.send_text_message')
    @patch('whatsapp.handlers.send_cta_url_message')
    def test_handle_stock_check_in_stock(self, mock_send_cta, mock_send_text):
        handle_stock_check("12345", "check stock SILK-SCARF-RED")
        mock_send_text.assert_called_once()
        mock_send_cta.assert_called_once()
        self.assertIn("✅ In Stock", mock_send_text.call_args[0][1])
        self.assertIn("10 units", mock_send_text.call_args[0][1])

    @patch('whatsapp.handlers.send_text_message')
    @patch('whatsapp.handlers.send_cta_url_message')
    def test_handle_stock_check_out_of_stock_with_reservations(self, mock_send_cta, mock_send_text):
        # Create reservations that exhaust stock
        Reservation.objects.create(
            variant_id=self.variant2.id,
            user_id="00000000-0000-0000-0000-000000000000",
            reserved_quantity=5,
            expires_at=timezone.now() + timedelta(minutes=10),
            status="active"
        )
        handle_stock_check("12345", "check stock SILK-SCARF-BLU")
        mock_send_text.assert_called_once()
        mock_send_cta.assert_called_once()
        self.assertIn("❌ Out of Stock", mock_send_text.call_args[0][1])
        self.assertIn("0 units", mock_send_text.call_args[0][1])

    @patch('whatsapp.handlers.send_text_message')
    def test_handle_stock_check_invalid_sku(self, mock_send_text):
        handle_stock_check("12345", "check stock INVALID-SKU")
        mock_send_text.assert_called_once()
        self.assertIn("was not found", mock_send_text.call_args[0][1])

    @patch('whatsapp.handlers.send_text_message')
    def test_handle_stock_check_no_sku(self, mock_send_text):
        handle_stock_check("12345", "check stock")
        mock_send_text.assert_called_once()
        self.assertIn("Please share the SKU code", mock_send_text.call_args[0][1])

    @patch('whatsapp.handlers.send_text_message')
    def test_handle_order_status(self, mock_send_text):
        handle_order_status("12345", "12345")
        mock_send_text.assert_called_once()
        self.assertIn("track your order", mock_send_text.call_args[0][1])

    @patch('whatsapp.handlers.send_text_message')
    def test_handle_fallback(self, mock_send_text):
        handle_fallback("12345")
        mock_send_text.assert_called_once()
        self.assertIn("Dwarikas assistant", mock_send_text.call_args[0][1])


@override_settings(
    WA_VERIFY_TOKEN='test_verify_token',
    WA_APP_SECRET='test_app_secret'
)
class WhatsAppWebhookViewTests(TestCase):

    def setUp(self):
        # We need to monkeypatch views.WA_VERIFY_TOKEN and views.WA_APP_SECRET
        # since override_settings doesn't automatically re-evaluate module-level variables
        from whatsapp import views
        self.original_verify = views.WA_VERIFY_TOKEN
        self.original_secret = views.WA_APP_SECRET
        views.WA_VERIFY_TOKEN = 'test_verify_token'
        views.WA_APP_SECRET = 'test_app_secret'

    def tearDown(self):
        from whatsapp import views
        views.WA_VERIFY_TOKEN = self.original_verify
        views.WA_APP_SECRET = self.original_secret

    def test_get_webhook_verification_success(self):
        url = reverse('whatsapp-webhook') + "?hub.mode=subscribe&hub.verify_token=test_verify_token&hub.challenge=XYZ"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "XYZ")

    def test_get_webhook_verification_failure(self):
        url = reverse('whatsapp-webhook') + "?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=XYZ"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), "Verification failed.")

    def test_post_webhook_invalid_signature(self):
        url = reverse('whatsapp-webhook')
        payload = {"entry": []}
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=invalid_signature"
        )
        self.assertEqual(response.status_code, 401)

    def test_post_webhook_missing_signature(self):
        url = reverse('whatsapp-webhook')
        payload = {"entry": []}
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 401)

    @patch('whatsapp.views.handle_catalog')
    def test_post_webhook_valid_signature_catalog_intent(self, mock_handle_catalog):
        url = reverse('whatsapp-webhook')
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15555555555",
                                    "phone_number_id": "123456789"
                                },
                                "contacts": [{"profile": {"name": "Test User"}, "wa_id": "12345"}],
                                "messages": [
                                    {
                                        "from": "12345",
                                        "id": "wamid.HBgLMjMzNzg0NTQ2MTEVAgASGBIwRDQ4NzhDM0E4RjkzRjAyOUQA",
                                        "timestamp": "1645600000",
                                        "text": {"body": "Please show catalog"},
                                        "type": "text"
                                    }
                                ]
                            },
                            "field": "messages"
                        }
                    ]
                }
            ]
        }
        body = json.dumps(payload)
        signature = 'sha256=' + hmac.new(
            b'test_app_secret', body.encode(), hashlib.sha256
        ).hexdigest()

        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        mock_handle_catalog.assert_called_once_with("12345")

    def test_post_webhook_status_update_ignored(self):
        url = reverse('whatsapp-webhook')
        # Delivery receipt payload contains statuses but no messages
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15555555555",
                                    "phone_number_id": "123456789"
                                },
                                "statuses": [
                                    {
                                        "id": "wamid.HBgLMjMzNzg0NTQ2MTEVAgASGBIwRDQ4NzhDM0E4RjkzRjAyOUQA",
                                        "status": "delivered",
                                        "timestamp": "1645600000",
                                        "recipient_id": "12345"
                                    }
                                ]
                            },
                            "field": "messages"
                        }
                    ]
                }
            ]
        }
        body = json.dumps(payload)
        signature = 'sha256=' + hmac.new(
            b'test_app_secret', body.encode(), hashlib.sha256
        ).hexdigest()

        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ignored'})

    def test_post_webhook_invalid_json(self):
        url = reverse('whatsapp-webhook')
        body = "invalid-json"
        signature = 'sha256=' + hmac.new(
            b'test_app_secret', body.encode(), hashlib.sha256
        ).hexdigest()

        response = self.client.post(
            url,
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid JSON.'})
