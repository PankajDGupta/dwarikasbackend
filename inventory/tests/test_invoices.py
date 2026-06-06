import jwt
import tempfile
import shutil
from unittest.mock import patch, PropertyMock
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile

from inventory.models import PurchaseInvoice, InvoiceLineItem
from inventory.gcs_service import upload_invoice_to_gcs, generate_signed_url
from inventory.tasks_service import enqueue_invoice_processing


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
class InvoiceIngestionTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.customer_token = generate_test_jwt("customer")
        self.staff_token = generate_test_jwt("staff")
        self.manager_token = generate_test_jwt("manager")

        # Create a test invoice for retrieval testing
        self.invoice = PurchaseInvoice.objects.create(
            gcs_object_path="gs://my-bucket/invoices/test-uuid/invoice.pdf",
            status="pending",
            uploaded_by="11111111-2222-3333-4444-555555555555"
        )
        self.line_item = InvoiceLineItem.objects.create(
            invoice=self.invoice,
            sku="SKU123",
            description="Test item",
            quantity=10,
            unit_price=150.00,
            gst_rate=18.00,
            confidence_score=0.985,
            needs_review=False
        )

    @patch('inventory.invoice_views.upload_invoice_to_gcs')
    @patch('inventory.invoice_views.enqueue_invoice_processing')
    def test_upload_success_for_staff(self, mock_enqueue, mock_upload):
        """Staff can upload a valid file, which saves database records and calls services."""
        mock_upload.return_value = "gs://mock-bucket/invoices/test.jpg"
        mock_enqueue.return_value = "projects/local-project/locations/local-loc/queues/local-queue/tasks/mock-task"

        # Create a small valid JPEG in memory
        file_content = b"fake-jpeg-data"
        uploaded_file = SimpleUploadedFile("invoice.jpg", file_content, content_type="image/jpeg")

        url = reverse("invoice-upload")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, {"file": uploaded_file}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], "processing")
        self.assertEqual(response.data["task_name"], mock_enqueue.return_value)
        self.assertIn("invoice_id", response.data)

        # Verify DB entry
        invoice_id = response.data["invoice_id"]
        invoice = PurchaseInvoice.objects.get(id=invoice_id)
        self.assertEqual(invoice.status, "processing")
        self.assertEqual(invoice.gcs_object_path, "gs://mock-bucket/invoices/test.jpg")

        # Verify mocked calls
        mock_upload.assert_called_once()
        mock_enqueue.assert_called_once_with(invoice_id=str(invoice.id), gcs_object_path=invoice.gcs_object_path)

    def test_unauthenticated_returns_401(self):
        """Request without token returns 401."""
        url = reverse("invoice-upload")
        response = self.client.post(url, {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_returns_403(self):
        """Request with customer role token returns 403."""
        url = reverse("invoice-upload")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(url, {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('inventory.invoice_views.upload_invoice_to_gcs')
    @patch('inventory.invoice_views.enqueue_invoice_processing')
    def test_invalid_content_type_returns_415(self, mock_enqueue, mock_upload):
        """Uploading unsupported files (e.g. txt) returns 415."""
        uploaded_file = SimpleUploadedFile("invoice.txt", b"plain text data", content_type="text/plain")

        url = reverse("invoice-upload")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, {"file": uploaded_file}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        mock_upload.assert_not_called()
        mock_enqueue.assert_not_called()

    @patch('django.core.files.uploadedfile.UploadedFile.size', new_callable=PropertyMock)
    @patch('inventory.invoice_views.upload_invoice_to_gcs')
    @patch('inventory.invoice_views.enqueue_invoice_processing')
    def test_file_too_large_returns_413(self, mock_enqueue, mock_upload, mock_size):
        """Files larger than 20MB return 413."""
        mock_size.return_value = 21 * 1024 * 1024  # 21 MB
        mock_upload.return_value = "gs://mock-bucket/invoices/test.jpg"
        mock_enqueue.return_value = "projects/local-project/locations/local-loc/queues/local-queue/tasks/mock-task"

        uploaded_file = SimpleUploadedFile("large.pdf", b"data", content_type="application/pdf")

        url = reverse("invoice-upload")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(url, {"file": uploaded_file}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        mock_upload.assert_not_called()
        mock_enqueue.assert_not_called()

    def test_list_invoices_for_staff(self):
        """Staff can list all invoices."""
        url = reverse("invoice-list")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get("results", response.data)
        self.assertTrue(len(results) >= 1)
        self.assertEqual(results[0]["id"], str(self.invoice.id))

    def test_retrieve_invoice_detail_for_staff(self):
        """Staff can retrieve a single invoice along with line items and signed_url."""
        url = reverse("invoice-detail", kwargs={"id": self.invoice.id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.invoice.id))
        self.assertEqual(response.data["signed_url"], self.invoice.gcs_object_path)
        self.assertTrue(len(response.data["line_items"]) == 1)
        self.assertEqual(response.data["line_items"][0]["sku"], "SKU123")


class GcsServiceLocalFallbackTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_base_dir = settings.BASE_DIR
        settings.BASE_DIR = self.temp_dir

    def tearDown(self):
        settings.BASE_DIR = self.orig_base_dir
        shutil.rmtree(self.temp_dir)

    def test_upload_and_sign_local_fallback(self):
        """GCS Service falls back to local file writes when GCS_INVOICE_BUCKET is unset."""
        with patch('inventory.gcs_service.GCS_BUCKET_NAME', None):
            file_data = b"my local file content"
            uploaded_file = SimpleUploadedFile("bill.jpg", file_data, content_type="image/jpeg")

            path_uri = upload_invoice_to_gcs(uploaded_file, "bill.jpg", "image/jpeg")
            self.assertTrue(path_uri.startswith("file:///"))

            file_path = path_uri.replace("file:///", "")
            import os
            self.assertTrue(os.path.exists(file_path))
            with open(file_path, 'rb') as f:
                self.assertEqual(f.read(), file_data)

            signed_url = generate_signed_url(path_uri)
            self.assertEqual(signed_url, path_uri)


class CloudTasksLocalFallbackTests(TestCase):
    def test_enqueue_local_fallback(self):
        """Cloud Tasks helper spawns a local simulated worker request when tasks params are unset."""
        with patch('inventory.tasks_service.CLOUD_TASKS_PROJECT', None):
            with patch('inventory.tasks_service._local_worker_dispatch') as mock_dispatch:
                task_name = enqueue_invoice_processing("invoice-uuid", "gs://mock-bucket/invoice.pdf")
                self.assertTrue(task_name.startswith("projects/local-project/locations/local-loc/queues/local-queue/tasks/"))
                mock_dispatch.assert_called_once()
                args, kwargs = mock_dispatch.call_args
                self.assertEqual(args[0], "http://127.0.0.1:8000/api/v1/tasks/process-invoice/")
                self.assertEqual(args[1], {
                    'invoice_id': 'invoice-uuid',
                    'gcs_object_path': 'gs://mock-bucket/invoice.pdf'
                })
