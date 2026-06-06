import json
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from django.conf import settings

from inventory.models import PurchaseInvoice, InvoiceLineItem
from tasks.image_preprocessing import preprocess_image
from tasks.document_ai_service import ExtractedInvoice, ExtractedLineItem, process_invoice_bytes


@override_settings(SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long")
class DocumentAIWorkerTests(TestCase):

    def setUp(self):
        # Create temp directory for local file read test
        self.temp_dir = tempfile.mkdtemp()
        self.orig_base_dir = settings.BASE_DIR
        settings.BASE_DIR = self.temp_dir

        # Create a dummy image file
        import numpy as np
        import cv2
        dummy_img = np.ones((100, 100, 3), dtype=np.uint8) * 255
        # Draw some text or lines on it to make it non-empty
        cv2.putText(dummy_img, "INVOICE", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        _, img_bytes = cv2.imencode('.jpg', dummy_img)
        self.dummy_image_bytes = img_bytes.tobytes()

        # Save dummy image to temp file
        self.temp_file_path = os.path.join(self.temp_dir, 'test_invoice.jpg')
        with open(self.temp_file_path, 'wb') as f:
            f.write(self.dummy_image_bytes)
        
        self.local_gcs_path = f"file:///{self.temp_file_path.replace(os.sep, '/')}"

        # Create a test invoice record
        self.invoice = PurchaseInvoice.objects.create(
            gcs_object_path=self.local_gcs_path,
            status="pending",
            uploaded_by="11111111-2222-3333-4444-555555555555"
        )
        self.url = reverse("task-process-invoice")

    def tearDown(self):
        settings.BASE_DIR = self.orig_base_dir
        shutil.rmtree(self.temp_dir)

    def test_direct_browser_call_without_header_returns_403(self):
        """Direct API call without X-CloudTasks-TaskName header is forbidden."""
        payload = {
            "invoice_id": str(self.invoice.id),
            "gcs_object_path": self.invoice.gcs_object_path
        }
        response = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json(), {"error": "Forbidden. Not a Cloud Tasks request."})

    def test_invalid_payload_returns_400(self):
        """Task payload missing invoice_id or bad json returns 400."""
        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        response = self.client.post(self.url, data="invalid-json", content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post(self.url, data=json.dumps({"bad_key": "val"}), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invoice_not_found_returns_404(self):
        """Task returns 404 if the invoice_id doesn't exist in the database."""
        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        payload = {
            "invoice_id": "00000000-0000-0000-0000-000000000000",
            "gcs_object_path": self.invoice.gcs_object_path
        }
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_invoice_already_confirmed_is_skipped(self):
        """If an invoice status is already confirmed, skip parsing (returns 200)."""
        self.invoice.status = "confirmed"
        self.invoice.save()

        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        payload = {
            "invoice_id": str(self.invoice.id),
            "gcs_object_path": self.invoice.gcs_object_path
        }
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"message": "Invoice already confirmed. Skipping."})

    @patch('tasks.views.process_invoice_bytes')
    def test_successful_parsing_needing_review(self, mock_process):
        """Task view successfully downloads, preprocesses, calls Document AI, and sets 'review' status if any item needs review."""
        mock_process.return_value = ExtractedInvoice(
            invoice_number="INV-100",
            vendor_name="Lal Qila Rice",
            vendor_gstin="07AABCU9603R1ZX",
            issued_at="2026-06-01",
            line_items=[
                ExtractedLineItem(
                    sku="SKU-123",
                    description="Rice bag",
                    quantity=5,
                    unit_price=100.00,
                    gst_rate=18.00,
                    confidence_score=0.95,
                    needs_review=False
                ),
                ExtractedLineItem(
                    sku="SKU-456",
                    description="Wheat bag",
                    quantity=2,
                    unit_price=80.00,
                    gst_rate=5.00,
                    confidence_score=0.85,  # Low confidence
                    needs_review=True
                )
            ]
        )

        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        payload = {
            "invoice_id": str(self.invoice.id),
            "gcs_object_path": self.invoice.gcs_object_path
        }
        
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify DB invoice updates
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "review")
        self.assertEqual(self.invoice.invoice_number, "INV-100")
        self.assertEqual(self.invoice.vendor_name, "Lal Qila Rice")
        self.assertEqual(self.invoice.vendor_gstin, "07AABCU9603R1ZX")
        self.assertEqual(str(self.invoice.issued_at), "2026-06-01")

        # Verify DB line items insertion
        items = InvoiceLineItem.objects.filter(invoice=self.invoice)
        self.assertEqual(items.count(), 2)
        item1 = items.get(sku="SKU-123")
        self.assertEqual(item1.quantity, 5)
        self.assertEqual(item1.needs_review, False)

        item2 = items.get(sku="SKU-456")
        self.assertEqual(item2.quantity, 2)
        self.assertEqual(item2.needs_review, True)

    @patch('tasks.views.process_invoice_bytes')
    def test_successful_parsing_confirmed(self, mock_process):
        """Task view sets 'confirmed' status if all items have high confidence."""
        mock_process.return_value = ExtractedInvoice(
            invoice_number="INV-200",
            vendor_name="Lal Qila Rice",
            vendor_gstin="07AABCU9603R1ZX",
            issued_at="2026-06-01",
            line_items=[
                ExtractedLineItem(
                    sku="SKU-123",
                    description="Rice bag",
                    quantity=5,
                    unit_price=100.00,
                    gst_rate=18.00,
                    confidence_score=0.98,
                    needs_review=False
                )
            ]
        )

        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        payload = {
            "invoice_id": str(self.invoice.id),
            "gcs_object_path": self.invoice.gcs_object_path
        }
        
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "confirmed")

    @patch('tasks.views.process_invoice_bytes')
    def test_idempotency_deletes_previous_line_items(self, mock_process):
        """Rerunning the task deletes any existing line items for the same invoice."""
        # Create an existing line item in DB linked to this invoice
        InvoiceLineItem.objects.create(
            invoice=self.invoice,
            sku="SKU-OLD",
            description="Old item",
            quantity=1,
            unit_price=10.0,
            confidence_score=0.99
        )

        mock_process.return_value = ExtractedInvoice(
            invoice_number="INV-300",
            vendor_name="Lal Qila Rice",
            line_items=[
                ExtractedLineItem(
                    sku="SKU-NEW",
                    description="New item",
                    quantity=10,
                    unit_price=20.00,
                    confidence_score=0.99,
                    needs_review=False
                )
            ]
        )

        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        payload = {
            "invoice_id": str(self.invoice.id),
            "gcs_object_path": self.invoice.gcs_object_path
        }
        
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify only SKU-NEW exists now, and SKU-OLD is deleted
        items = InvoiceLineItem.objects.filter(invoice=self.invoice)
        self.assertEqual(items.count(), 1)
        self.assertEqual(items.first().sku, "SKU-NEW")

    def test_file_download_failure_sets_status_failed(self):
        """If GCS download or local file read fails, status becomes 'failed'."""
        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        payload = {
            "invoice_id": str(self.invoice.id),
            "gcs_object_path": "file:///nonexistent/path/to/invoice.jpg"
        }
        
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "failed")

    @patch('tasks.views.process_invoice_bytes')
    def test_document_ai_failure_sets_status_failed(self, mock_process):
        """If Document AI api call fails, status becomes 'failed'."""
        mock_process.side_effect = RuntimeError("Document AI service offline")

        headers = {"HTTP_X_CloudTasks_TaskName": "mock-task-name"}
        payload = {
            "invoice_id": str(self.invoice.id),
            "gcs_object_path": self.invoice.gcs_object_path
        }
        
        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "failed")

    def test_image_preprocessing_function(self):
        """preprocess_image function runs successfully on valid image bytes."""
        out_bytes = preprocess_image(self.dummy_image_bytes)
        self.assertIsInstance(out_bytes, bytes)
        self.assertTrue(len(out_bytes) > 0)
