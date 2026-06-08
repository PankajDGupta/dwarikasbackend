import os
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.conf import settings
from django.db import DatabaseError, transaction, OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import Product, ProductVariant, Reservation, Order
from pos.models import PosCart, PosCartItem, OrderItem


def _jwt(role, user_id="cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa", email="staff@test.com"):
    """Generate a HS256 JWT matching the SupabaseJWTAuthentication expectations."""
    payload = {
        "aud": "authenticated",
        "sub": user_id,
        "email": email,
        "app_metadata": {"role": role},
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


CUSTOMER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
STAFF_UUID    = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa"
MANAGER_UUID  = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class PosFeatureTests(TestCase):
    """Tests for Spec 18 — POS Cash Sales & In-Store Bill Generation."""

    def setUp(self):
        self.client = APIClient()

        # URLs
        self.create_cart_url = reverse('pos-cart-create')
        
        # Setup catalog items
        self.product = Product.objects.create(
            name="Kirana Brand Rice",
            hsn_code="10063020",
            gst_slab=Decimal("18.00"),
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-POS-RICE",
            stock_quantity=50,
            retail_price=Decimal("100.00"),
        )
        self.variant_two = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-POS-RICE-2",
            stock_quantity=20,
            retail_price=Decimal("80.00"),
        )

        self.customer_token = _jwt("customer", CUSTOMER_UUID)
        self.staff_token = _jwt("staff", STAFF_UUID)
        self.manager_token = _jwt("manager", MANAGER_UUID)

        # Set environment variables for store config
        os.environ['STORE_NAME'] = 'Dwarikas Store'
        os.environ['STORE_ADDRESS'] = '123 Retail Lane, Bangalore'
        os.environ['STORE_GSTIN'] = '29AAAAA0000A1Z5'

    def tearDown(self):
        # Clean up environment variables
        os.environ.pop('STORE_NAME', None)
        os.environ.pop('STORE_ADDRESS', None)
        os.environ.pop('STORE_GSTIN', None)

    def _cart_items_url(self, cart_id):
        return reverse('pos-cart-item-add', kwargs={'cart_id': cart_id})

    def _cart_item_remove_url(self, cart_id, item_id):
        return reverse('pos-cart-item-remove', kwargs={'cart_id': cart_id, 'item_id': item_id})

    def _cart_detail_url(self, cart_id):
        return reverse('pos-cart-detail', kwargs={'cart_id': cart_id})

    def _cart_confirm_url(self, cart_id):
        return reverse('pos-cart-confirm', kwargs={'cart_id': cart_id})

    def _cart_abandon_url(self, cart_id):
        return reverse('pos-cart-abandon', kwargs={'cart_id': cart_id})

    def _bill_url(self, order_id):
        return reverse('pos-bill', kwargs={'order_id': order_id})

    # ── 1. Security Guards (RBAC check) ──────────────────────────────────────────

    def test_unauthenticated_request_returns_401(self):
        response = self.client.post(self.create_cart_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_token_returns_403(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(self.create_cart_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_and_manager_tokens_return_201(self):
        # Staff
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(self.create_cart_url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'open')
        
        # Manager
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response2 = self.client.post(self.create_cart_url)
        self.assertEqual(response2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response2.data['status'], 'open')

    # ── 2. Cart Creation & Phone Mapping ─────────────────────────────────────────

    def test_cart_created_with_customer_phone_stores_correctly(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        phone = "9988776655"
        response = self.client.post(self.create_cart_url, data={'customer_phone': phone})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        cart_id = response.data['cart_id']
        cart = PosCart.objects.get(id=cart_id)
        self.assertEqual(cart.customer_phone, phone)
        self.assertEqual(str(cart.staff_user_id), STAFF_UUID)

    # ── 3. Cart Items Operations (CRUD & Upsert) ──────────────────────────────────

    def test_add_and_update_cart_items_upsert_logic(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        
        # Create cart
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        # Add item first time
        items_url = self._cart_items_url(cart_id)
        response = self.client.post(items_url, data={
            'variant_id': str(self.variant.id),
            'quantity': 2
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['quantity'], 2)
        self.assertEqual(response.data['variant_id'], str(self.variant.id))
        self.assertEqual(response.data['sku'], self.variant.sku)
        self.assertEqual(response.data['line_subtotal'], '200.00')

        # Check in DB
        self.assertEqual(PosCartItem.objects.filter(cart_id=cart_id).count(), 1)

        # Add same item again (quantity updates to 5)
        response2 = self.client.post(items_url, data={
            'variant_id': str(self.variant.id),
            'quantity': 5
        })
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.data['quantity'], 5)
        self.assertEqual(response2.data['line_subtotal'], '500.00')

        # Check in DB (still only 1 row)
        self.assertEqual(PosCartItem.objects.filter(cart_id=cart_id).count(), 1)
        item = PosCartItem.objects.get(cart_id=cart_id)
        self.assertEqual(item.quantity, 5)

    def test_remove_cart_item(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        # Add item
        add_res = self.client.post(self._cart_items_url(cart_id), data={
            'variant_id': str(self.variant.id),
            'quantity': 1
        })
        item_id = add_res.data['item_id']

        # Delete item
        del_res = self.client.delete(self._cart_item_remove_url(cart_id, item_id))
        self.assertEqual(del_res.status_code, status.HTTP_204_NO_CONTENT)

        # Check DB
        self.assertFalse(PosCartItem.objects.filter(id=item_id).exists())

    # ── 4. Cart Detail View & Live ATP Check ──────────────────────────────────────

    def test_cart_detail_displays_totals_and_atp_checks(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        # 1. Create a timed reservation for customer (reduces ATP for this SKU)
        now = datetime.now(timezone.utc)
        Reservation.objects.create(
            variant=self.variant,
            user_id=uuid.UUID(CUSTOMER_UUID),
            reserved_quantity=5,
            expires_at=now + timedelta(minutes=10),
            status='active',
        )

        # Stock is 50, reservation is 5 -> ATP should be 45

        # Add items to POS Cart
        self.client.post(self._cart_items_url(cart_id), data={
            'variant_id': str(self.variant.id),
            'quantity': 10
        })
        self.client.post(self._cart_items_url(cart_id), data={
            'variant_id': str(self.variant_two.id),
            'quantity': 2
        })

        # Fetch detail
        response = self.client.get(self._cart_detail_url(cart_id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.data
        self.assertEqual(data['cart_id'], cart_id)
        self.assertEqual(len(data['items']), 2)

        # Item 1 details: SKU-POS-RICE
        item1 = next(i for i in data['items'] if i['sku'] == self.variant.sku)
        self.assertEqual(item1['quantity'], 10)
        self.assertEqual(item1['unit_price'], '100.00')
        self.assertEqual(item1['subtotal'], '1000.00')
        self.assertEqual(item1['gst_amount'], '180.00') # 1000 * 18% = 180
        self.assertEqual(item1['line_total'], '1180.00')
        self.assertEqual(item1['atp_available'], 45) # 50 - 5
        self.assertTrue(item1['stock_ok'])

        # Item 2 details: SKU-POS-RICE-2
        item2 = next(i for i in data['items'] if i['sku'] == self.variant_two.sku)
        self.assertEqual(item2['quantity'], 2)
        self.assertEqual(item2['unit_price'], '80.00')
        self.assertEqual(item2['subtotal'], '160.00')
        self.assertEqual(item2['gst_amount'], '28.80') # 160 * 18% = 28.80
        self.assertEqual(item2['line_total'], '188.80')
        self.assertEqual(item2['atp_available'], 20)
        self.assertTrue(item2['stock_ok'])

        # Grand Totals:
        # Subtotal: 1000 + 160 = 1160
        # GST: 180 + 28.80 = 208.80
        # Grand: 1160 + 208.80 = 1368.80
        self.assertEqual(data['totals']['subtotal'], '1160.00')
        self.assertEqual(data['totals']['total_gst'], '208.80')
        self.assertEqual(data['totals']['grand_total'], '1368.80')

    # ── 5. Cart Confirmation (Stock Locking, Order, Bill, Rounding) ─────────────

    def test_cart_confirmation_success_flow(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url, data={'customer_phone': '9876543210'})
        cart_id = cart_res.data['cart_id']

        # Add items
        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant.id), 'quantity': 3})
        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant_two.id), 'quantity': 1})

        # Totals:
        # Item 1 sub: 300, gst: 54, total: 354
        # Item 2 sub: 80, gst: 14.40, total: 94.40
        # Total sub: 380, gst: 68.40, grand: 448.40

        # Confirm checkout
        response = self.client.post(self._cart_confirm_url(cart_id), data={
            'cash_tendered': '500.00',
            'payment_method': 'cash'
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify receipt payload fields
        data = response.data
        self.assertEqual(data['bill_type'], 'POS_CASH')
        self.assertEqual(data['customer_phone'], '9876543210')
        self.assertEqual(data['totals']['subtotal'], '380.00')
        self.assertEqual(data['totals']['total_gst'], '68.40')
        self.assertEqual(data['totals']['grand_total'], '448.40')
        
        # GST Split (Intra-state CGST/SGST = GST total / 2)
        # Total CGST: 27 + 7.20 = 34.20
        # Total SGST: 27 + 7.20 = 34.20
        self.assertEqual(data['totals']['total_cgst'], '34.20')
        self.assertEqual(data['totals']['total_sgst'], '34.20')

        # Payment details
        self.assertEqual(data['payment']['method'], 'cash')
        self.assertEqual(data['payment']['cash_tendered'], '500.00')
        self.assertEqual(data['payment']['change_due'], '51.60') # 500 - 448.40 = 51.60

        # Store details
        self.assertEqual(data['store']['name'], 'Dwarikas Store')
        self.assertEqual(data['store']['address'], '123 Retail Lane, Bangalore')
        self.assertEqual(data['store']['gstin'], '29AAAAA0000A1Z5')

        # Check DB states
        # 1. Cart status updated to confirmed
        cart = PosCart.objects.get(id=cart_id)
        self.assertEqual(cart.status, 'confirmed')

        # 2. Stock decremented:
        # Variant 1: 50 - 3 = 47
        # Variant 2: 20 - 1 = 19
        self.variant.refresh_from_db()
        self.variant_two.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 47)
        self.assertEqual(self.variant_two.stock_quantity, 19)

        # 3. Order and OrderItems created
        order_id = data['order_id']
        order = Order.objects.get(id=order_id)
        self.assertNil = False
        self.assertEqual(order.total_amount, Decimal('448.40'))
        self.assertEqual(order.gst_amount, Decimal('68.40'))
        self.assertEqual(order.payment_method, 'cash')
        self.assertEqual(order.payment_status, 'completed')

        # Order Items
        items = list(OrderItem.objects.filter(order=order))
        self.assertEqual(len(items), 2)
        
        oi1 = next(x for x in items if x.sku_snapshot == self.variant.sku)
        self.assertEqual(oi1.product_name_snapshot, self.product.name)
        self.assertEqual(oi1.hsn_code_snapshot, self.product.hsn_code)
        self.assertEqual(oi1.gst_slab_snapshot, self.product.gst_slab)
        self.assertEqual(oi1.quantity, 3)
        self.assertEqual(oi1.unit_price, Decimal('100.00'))
        self.assertEqual(oi1.subtotal, Decimal('300.00'))
        self.assertEqual(oi1.gst_amount, Decimal('54.00'))
        self.assertEqual(oi1.line_total, Decimal('354.00'))

    # ── 6. Cart Confirmation (ATP Stock Insufficiency Guard) ───────────────────

    def test_cart_confirmation_fails_on_insufficient_atp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        # Add items (quantity requested: 60, but stock is 50)
        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant.id), 'quantity': 60})

        # Confirm
        response = self.client.post(self._cart_confirm_url(cart_id), data={
            'cash_tendered': '6000.00',
            'payment_method': 'cash'
        })
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('Insufficient stock', response.data['error'])
        self.assertEqual(response.data['insufficient_items'][0]['sku'], self.variant.sku)
        self.assertEqual(response.data['insufficient_items'][0]['atp'], 50)

        # Verify DB is unchanged
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, 50)
        
        cart = PosCart.objects.get(id=cart_id)
        self.assertEqual(cart.status, 'open')

    # ── 7. Cart Confirmation (Validation Checks) ─────────────────────────────────

    def test_confirm_cashier_payment_shortfall_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant.id), 'quantity': 1})

        # Total is 118.00 (100 subtotal + 18 gst)
        # Cash tendered is 100.00 (shortfall of 18)
        response = self.client.post(self._cart_confirm_url(cart_id), data={
            'cash_tendered': '100.00',
            'payment_method': 'cash'
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('shortfall', response.data)
        self.assertEqual(response.data['shortfall'], '18.00')

    def test_confirm_invalid_payment_method_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant.id), 'quantity': 1})

        response = self.client.post(self._cart_confirm_url(cart_id), data={
            'cash_tendered': '200.00',
            'payment_method': 'UPI' # POS confirm only supports cash
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── 8. Reprinting Historical POS Bill ───────────────────────────────────────

    def test_reprint_historical_invoice(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant.id), 'quantity': 1})

        confirm_res = self.client.post(self._cart_confirm_url(cart_id), data={
            'cash_tendered': '200.00',
            'payment_method': 'cash'
        })
        order_id = confirm_res.data['order_id']

        # Fetch bill
        bill_res = self.client.get(self._bill_url(order_id))
        self.assertEqual(bill_res.status_code, status.HTTP_200_OK)
        self.assertEqual(bill_res.data['order_id'], order_id)
        self.assertEqual(bill_res.data['totals']['grand_total'], '118.00')
        self.assertEqual(len(bill_res.data['line_items']), 1)

    # ── 9. Abandoning POS Cart ──────────────────────────────────────────────────

    def test_abandon_cart_transitions_status(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        abandon_res = self.client.delete(self._cart_abandon_url(cart_id))
        self.assertEqual(abandon_res.status_code, status.HTTP_204_NO_CONTENT)

        cart = PosCart.objects.get(id=cart_id)
        self.assertEqual(cart.status, 'abandoned')

    # ── 10. Snapshot Integrity (deletion test) ──────────────────────────────────

    def test_snapshots_survive_catalog_deletions_or_edits(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant.id), 'quantity': 1})
        confirm_res = self.client.post(self._cart_confirm_url(cart_id), data={
            'cash_tendered': '200.00',
            'payment_method': 'cash'
        })
        order_id = confirm_res.data['order_id']

        # Simulate modifying catalog: change unit price and gst slab
        self.product.gst_slab = Decimal("5.00")
        self.product.save()
        self.variant.retail_price = Decimal("150.00")
        self.variant.save()

        # Re-fetch bill: it should retain original snapshotted values (gst_slab=18.00, price=100.00)
        bill_res = self.client.get(self._bill_url(order_id))
        self.assertEqual(bill_res.status_code, status.HTTP_200_OK)
        
        item = bill_res.data['line_items'][0]
        self.assertEqual(item['gst_slab'], '18.00')
        self.assertEqual(item['unit_price'], '100.00')
        self.assertEqual(item['line_total'], '118.00')

    # ── 11. Concurrency Safety and Contention Lock Handling ──────────────────────

    def test_confirm_handles_lock_contention(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        cart_res = self.client.post(self.create_cart_url)
        cart_id = cart_res.data['cart_id']

        self.client.post(self._cart_items_url(cart_id), data={'variant_id': str(self.variant.id), 'quantity': 1})

        # Mock select_for_update to raise OperationalError (simulating lock timeout/conflict)
        with patch('django.db.models.query.QuerySet.select_for_update') as mock_select:
            mock_select.side_effect = OperationalError("Could not obtain lock")
            
            response = self.client.post(self._cart_confirm_url(cart_id), data={
                'cash_tendered': '200.00',
                'payment_method': 'cash'
            })
            self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
            self.assertIn('retry', response.data['error'])
