import jwt
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient
from django.db import transaction

from inventory.models import PurchaseInvoice, InvoiceLineItem, Product, ProductVariant


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
class HitlInvoiceTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

        # Create products and variants for testing SKU match
        self.product = Product.objects.create(
            name="Test Product",
            hsn_code="123456",
            gst_slab=18.00,
            product_type="grocery"
        )
        self.variant1 = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-MATCHED-1",
            barcode="8901111111111",
            stock_quantity=10,
            retail_price=100.00
        )
        self.variant2 = ProductVariant.objects.create(
            product=self.product,
            sku="SKU-MATCHED-2",
            barcode="8901111111112",
            stock_quantity=5,
            retail_price=200.00
        )

        # Create test invoices in various statuses
        self.invoice_review = PurchaseInvoice.objects.create(
            invoice_number="INV-REV-001",
            vendor_name="Vendor A",
            vendor_gstin="07AAAAA1111A1Z1",
            issued_at="2026-06-01",
            gcs_object_path="file:///invoices/rev.pdf",
            status="review",
            uploaded_by="11111111-2222-3333-4444-555555555555"
        )
        self.item_rev1 = InvoiceLineItem.objects.create(
            invoice=self.invoice_review,
            sku="SKU-MATCHED-1",
            description="Item 1",
            quantity=5,
            unit_price=100.00,
            gst_rate=18.00,
            confidence_score=0.95,
            needs_review=False
        )
        self.item_rev2 = InvoiceLineItem.objects.create(
            invoice=self.invoice_review,
            sku="SKU-MATCHED-2",
            description="Item 2",
            quantity=10,
            unit_price=200.00,
            gst_rate=18.00,
            confidence_score=0.85,
            needs_review=True  # Low confidence, needs review
        )

        self.invoice_confirmed = PurchaseInvoice.objects.create(
            invoice_number="INV-CONF-001",
            vendor_name="Vendor B",
            vendor_gstin="07AAAAA1111A1Z1",
            issued_at="2026-06-01",
            gcs_object_path="file:///invoices/conf.pdf",
            status="confirmed",
            uploaded_by="11111111-2222-3333-4444-555555555555"
        )
        self.item_conf1 = InvoiceLineItem.objects.create(
            invoice=self.invoice_confirmed,
            sku="SKU-MATCHED-1",
            description="Item 1",
            quantity=5,
            unit_price=100.00,
            gst_rate=18.00,
            confidence_score=0.98,
            needs_review=False
        )

        self.invoice_pending = PurchaseInvoice.objects.create(
            invoice_number="INV-PEND-001",
            gcs_object_path="file:///invoices/pend.pdf",
            status="pending",
            uploaded_by="11111111-2222-3333-4444-555555555555"
        )

    # ── RBAC / Permissions Tests ──────────────────────────────────────────

    def test_review_endpoint_rbac(self):
        """Review endpoint requires IsStaffOrManager."""
        url = reverse("invoice-review", kwargs={"id": self.invoice_review.id})

        # Anonymous -> 401
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Staff -> 200
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Manager -> 200
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_line_item_endpoint_rbac(self):
        """Update line item endpoint requires IsStaffOrManager."""
        url = reverse("line-item-update", kwargs={"id": self.item_rev1.id})
        data = {"quantity": 8}

        # Anonymous -> 401
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Staff -> 200
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_confirm_endpoint_rbac(self):
        """Confirm endpoint requires IsStaffOrManager."""
        url = reverse("invoice-confirm", kwargs={"id": self.invoice_review.id})

        # Anonymous -> 401
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Staff -> or Manager -> allowed (checks are run on views)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        # Note: might fail with 422 because invoice has items needing review, but not 403.
        response = self.client.post(url)
        self.assertNotEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── InvoiceReviewView Tests ───────────────────────────────────────────

    def test_review_view_returns_correct_data(self):
        """GET /review/ returns signed_image_url and review_summary statistics."""
        url = reverse("invoice-review", kwargs={"id": self.invoice_review.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.invoice_review.id))
        self.assertEqual(response.data["invoice_number"], "INV-REV-001")
        self.assertEqual(response.data["vendor_name"], "Vendor A")

        # Validate signed image URL output
        self.assertEqual(response.data["signed_image_url"], self.invoice_review.gcs_object_path)

        # Validate summary stats
        self.assertEqual(response.data["review_summary"]["total_items"], 2)
        self.assertEqual(response.data["review_summary"]["needs_review_count"], 1)

        # Validate nested line items
        self.assertEqual(len(response.data["line_items"]), 2)
        self.assertEqual(response.data["line_items"][0]["sku"], "SKU-MATCHED-1")
        self.assertEqual(response.data["line_items"][0]["needs_review"], False)
        self.assertEqual(response.data["line_items"][1]["sku"], "SKU-MATCHED-2")
        self.assertEqual(response.data["line_items"][1]["needs_review"], True)

    def test_review_view_not_found(self):
        """GET /review/ returns 404 for non-existent invoice."""
        non_existent_id = "00000000-0000-0000-0000-000000000000"
        url = reverse("invoice-review", kwargs={"id": non_existent_id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"], "Invoice not found.")

    # ── LineItemUpdateView Tests ──────────────────────────────────────────

    def test_patch_line_item_updates_fields_and_clears_needs_review(self):
        """PATCH updates line item details and clears the needs_review flag."""
        url = reverse("line-item-update", kwargs={"id": self.item_rev2.id})
        data = {
            "sku": "SKU-MATCHED-2-EDITED",
            "quantity": 12,
            "unit_price": "220.00",
            "gst_rate": "12.00"
        }
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.patch(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["sku"], "SKU-MATCHED-2-EDITED")
        self.assertEqual(int(response.data["quantity"]), 12)
        self.assertEqual(float(response.data["unit_price"]), 220.00)
        self.assertEqual(float(response.data["gst_rate"]), 12.00)

        # Check needs_review is automatically set to False
        self.assertEqual(response.data["needs_review"], False)

        # Check DB
        self.item_rev2.refresh_from_db()
        self.assertEqual(self.item_rev2.sku, "SKU-MATCHED-2-EDITED")
        self.assertEqual(self.item_rev2.needs_review, False)

    def test_patch_line_item_on_confirmed_invoice_fails(self):
        """Cannot edit line items of an already confirmed invoice."""
        url = reverse("line-item-update", kwargs={"id": self.item_conf1.id})
        data = {"quantity": 20}
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.patch(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"], "Cannot edit line items on a confirmed invoice.")

    # ── InvoiceConfirmView Tests ──────────────────────────────────────────

    def test_confirm_invoice_fails_when_items_need_review(self):
        """Confirmation is blocked (422) if any line items still have needs_review=True."""
        url = reverse("invoice-confirm", kwargs={"id": self.invoice_review.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("still require review before confirmation", response.data["error"])

    def test_confirm_invoice_fails_with_invalid_status(self):
        """Confirmation is blocked (409) if invoice is not in a confirmable status (e.g. pending)."""
        url = reverse("invoice-confirm", kwargs={"id": self.invoice_pending.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("cannot be confirmed in status 'pending'", response.data["error"])

    def test_confirm_invoice_idempotency(self):
        """Re-confirming an already confirmed invoice is a no-op returning 200."""
        # Record initial stock quantities of variants
        initial_stock_1 = self.variant1.stock_quantity

        url = reverse("invoice-confirm", kwargs={"id": self.invoice_confirmed.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Invoice was already confirmed. No stock changes applied.")

        # Ensure no stock increments were applied
        self.variant1.refresh_from_db()
        self.assertEqual(self.variant1.stock_quantity, initial_stock_1)

    def test_confirm_invoice_success_commits_stock(self):
        """POST /confirm/ on review invoice with clean line items increments stock and updates status."""
        # First clear needs_review on the second item to make confirmation allowed
        self.item_rev2.needs_review = False
        self.item_rev2.save()

        # Add a third item with an unmatched SKU to test unmatched logging
        InvoiceLineItem.objects.create(
            invoice=self.invoice_review,
            sku="SKU-UNMATCHED-999",
            description="Unmatched product",
            quantity=15,
            unit_price=10.00,
            gst_rate=18.00,
            confidence_score=0.99,
            needs_review=False
        )

        initial_stock_1 = self.variant1.stock_quantity  # 10
        initial_stock_2 = self.variant2.stock_quantity  # 5

        url = reverse("invoice-confirm", kwargs={"id": self.invoice_review.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "confirmed")
        self.assertEqual(response.data["stock_updates_applied"], 2)

        # SKU-MATCHED-1: initial 10, item quantity 5 -> 15
        self.variant1.refresh_from_db()
        self.assertEqual(self.variant1.stock_quantity, initial_stock_1 + 5)

        # SKU-MATCHED-2: initial 5, item quantity 10 -> 15
        self.variant2.refresh_from_db()
        self.assertEqual(self.variant2.stock_quantity, initial_stock_2 + 10)

        # Check that invoice status is now 'confirmed'
        self.invoice_review.refresh_from_db()
        self.assertEqual(self.invoice_review.status, "confirmed")

        # Check matched and unmatched SKUs logging in response
        self.assertIn("SKU-MATCHED-1", response.data["matched_skus"])
        self.assertIn("SKU-MATCHED-2", response.data["matched_skus"])

        unmatched = response.data["unmatched_skus"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["sku"], "SKU-UNMATCHED-999")
        self.assertEqual(unmatched[0]["reason"], "SKU not found in product_variants")

    def test_confirm_invoice_atomic_rollback(self):
        """Any database error during confirmation triggers rollback of stock and invoice status."""
        self.item_rev2.needs_review = False
        self.item_rev2.save()

        initial_stock_1 = self.variant1.stock_quantity

        url = reverse("invoice-confirm", kwargs={"id": self.invoice_review.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")

        # Force an integrity error or db exception when saving the invoice status update
        # by mocking ProductVariant update to raise a DatabaseError or similar exception.
        from django.db.utils import DatabaseError

        with patch("inventory.models.ProductVariant.objects.filter") as mock_filter:
            mock_filter.side_effect = DatabaseError("Mock database lock failure.")

            with self.assertRaises(DatabaseError):
                self.client.post(url)

        # Verify stock and invoice status are unchanged
        self.variant1.refresh_from_db()
        self.assertEqual(self.variant1.stock_quantity, initial_stock_1)

        self.invoice_review.refresh_from_db()
        self.assertEqual(self.invoice_review.status, "review")
