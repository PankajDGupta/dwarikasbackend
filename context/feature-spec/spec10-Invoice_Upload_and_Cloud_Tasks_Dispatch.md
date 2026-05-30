# Spec 10 — Invoice Upload & Cloud Tasks Dispatch

## Goal
Implement the first half of the Intelligent Document Processing (IDP) pipeline. When a warehouse staff member uploads a vendor invoice image/PDF, Django:
1. Validates and stores the file in a private GCS bucket
2. Records an `invoice_upload` record in the database with status `pending`
3. Enqueues a Cloud Tasks job pointing to the OCR worker endpoint (Spec 11)

The upload endpoint returns immediately — all heavy OCR processing happens asynchronously.

---

## Scope
- Extend the Supabase schema with two new tables: `purchase_invoices` and `invoice_line_items`
- `POST /api/v1/invoices/upload/` — upload a file to GCS and enqueue a Cloud Task (staff/manager only)
- `GET /api/v1/invoices/` — list all invoices with status (staff/manager)
- `GET /api/v1/invoices/<uuid:id>/` — get a single invoice with extracted line items

---

## Database Schema Extension (Supabase SQL — run manually)

```sql
-- Stores vendor invoice metadata and processing state
CREATE TABLE public.purchase_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number TEXT UNIQUE,
    vendor_name TEXT,
    vendor_gstin TEXT,
    issued_at DATE,
    gcs_object_path TEXT NOT NULL,          -- gs://bucket-name/path/to/file
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'review', 'confirmed', 'failed')),
    uploaded_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Individual line items extracted by the IDP pipeline from each invoice
CREATE TABLE public.invoice_line_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES public.purchase_invoices(id) ON DELETE CASCADE,
    sku TEXT,
    description TEXT,
    quantity INTEGER,
    unit_price NUMERIC(12, 2),
    gst_rate NUMERIC(5, 2),
    confidence_score NUMERIC(4, 3),          -- 0.000–1.000 from Document AI
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Files to Create / Modify

### `inventory/models.py` — Add invoice models

```python
class PurchaseInvoice(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('review', 'Needs Review'),
        ('confirmed', 'Confirmed'),
        ('failed', 'Failed'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.TextField(unique=True, null=True, blank=True)
    vendor_name = models.TextField(null=True, blank=True)
    vendor_gstin = models.TextField(null=True, blank=True)
    issued_at = models.DateField(null=True, blank=True)
    gcs_object_path = models.TextField()
    status = models.TextField(choices=STATUS_CHOICES, default='pending')
    uploaded_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'purchase_invoices'


class InvoiceLineItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name='line_items',
        db_column='invoice_id',
    )
    sku = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    quantity = models.IntegerField(null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    confidence_score = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    needs_review = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'invoice_line_items'
```

---

### `inventory/gcs_service.py` — New file

```python
"""
Google Cloud Storage helpers for invoice file management.
"""
import os
import uuid
from google.cloud import storage

GCS_BUCKET_NAME = os.environ.get('GCS_INVOICE_BUCKET')
_client = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def upload_invoice_to_gcs(file_obj, filename: str, content_type: str) -> str:
    """
    Uploads a file-like object to the private GCS invoice bucket.
    Returns the GCS object path: gs://<bucket>/<path>
    """
    client = _get_client()
    bucket = client.bucket(GCS_BUCKET_NAME)

    # Namespace files under a UUID subdirectory to prevent collisions
    object_name = f"invoices/{uuid.uuid4()}/{filename}"
    blob = bucket.blob(object_name)
    blob.upload_from_file(file_obj, content_type=content_type)

    return f"gs://{GCS_BUCKET_NAME}/{object_name}"


def generate_signed_url(gcs_object_path: str, expiry_minutes: int = 60) -> str:
    """
    Returns a time-limited signed URL for viewing an invoice image in the HITL UI.
    gcs_object_path format: gs://<bucket>/<object>
    """
    client = _get_client()
    # Parse bucket and object from path
    path_parts = gcs_object_path.replace('gs://', '').split('/', 1)
    bucket_name, object_name = path_parts[0], path_parts[1]
    blob = client.bucket(bucket_name).blob(object_name)

    from datetime import timedelta
    url = blob.generate_signed_url(expiration=timedelta(minutes=expiry_minutes), method='GET')
    return url
```

---

### `inventory/tasks_service.py` — New file

```python
"""
Google Cloud Tasks queue dispatcher.
"""
import json
import os
from google.cloud import tasks_v2

CLOUD_TASKS_PROJECT = os.environ.get('GCP_PROJECT_ID')
CLOUD_TASKS_LOCATION = os.environ.get('GCP_TASKS_LOCATION', 'asia-south1')
CLOUD_TASKS_QUEUE = os.environ.get('GCP_TASKS_QUEUE', 'invoice-processing-queue')
WORKER_URL = os.environ.get('TASKS_WORKER_URL')   # Full URL of the Cloud Tasks worker endpoint

_client = None


def _get_client() -> tasks_v2.CloudTasksClient:
    global _client
    if _client is None:
        _client = tasks_v2.CloudTasksClient()
    return _client


def enqueue_invoice_processing(invoice_id: str, gcs_object_path: str) -> str:
    """
    Pushes a lightweight metadata payload to the Cloud Tasks queue.
    The worker (Spec 11) polls this queue and runs the OCR pipeline.
    Returns the created task name.
    """
    client = _get_client()
    parent = client.queue_path(CLOUD_TASKS_PROJECT, CLOUD_TASKS_LOCATION, CLOUD_TASKS_QUEUE)

    payload = json.dumps({
        'invoice_id': invoice_id,
        'gcs_object_path': gcs_object_path,
    }).encode('utf-8')

    task = {
        'http_request': {
            'http_method': tasks_v2.HttpMethod.POST,
            'url': f"{WORKER_URL}/api/v1/tasks/process-invoice/",
            'headers': {'Content-Type': 'application/json'},
            'body': payload,
            # Cloud Tasks authenticates to Cloud Run using OIDC
            'oidc_token': {
                'service_account_email': os.environ.get('TASKS_SERVICE_ACCOUNT_EMAIL'),
            },
        }
    }

    created_task = client.create_task(request={'parent': parent, 'task': task})
    return created_task.name
```

---

### `inventory/invoice_views.py` — New file

```python
import uuid
from rest_framework import generics, status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsStaffOrManager
from inventory.models import PurchaseInvoice
from inventory.serializers import PurchaseInvoiceSerializer
from inventory.gcs_service import upload_invoice_to_gcs, generate_signed_url
from inventory.tasks_service import enqueue_invoice_processing

ALLOWED_CONTENT_TYPES = {'application/pdf', 'image/jpeg', 'image/png', 'image/webp'}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024   # 20 MB hard limit


class InvoiceUploadView(APIView):
    """
    POST /api/v1/invoices/upload/
    Accepts a multipart file upload, stores it in GCS, and dispatches an async OCR task.
    """
    permission_classes = [IsStaffOrManager]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({'error': 'No file provided. Key must be "file".'}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded_file.content_type not in ALLOWED_CONTENT_TYPES:
            return Response(
                {'error': f'Unsupported file type: {uploaded_file.content_type}. Allowed: PDF, JPEG, PNG, WebP.'},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        if uploaded_file.size > MAX_FILE_SIZE_BYTES:
            return Response({'error': 'File exceeds the 20 MB limit.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        # Upload to GCS
        gcs_path = upload_invoice_to_gcs(
            file_obj=uploaded_file,
            filename=uploaded_file.name,
            content_type=uploaded_file.content_type,
        )

        # Create invoice record in database
        uploader_id = uuid.UUID(request.user.username)
        invoice = PurchaseInvoice.objects.create(
            gcs_object_path=gcs_path,
            status='pending',
            uploaded_by=uploader_id,
        )

        # Dispatch async Cloud Tasks job
        task_name = enqueue_invoice_processing(
            invoice_id=str(invoice.id),
            gcs_object_path=gcs_path,
        )

        # Immediately update status to 'processing'
        invoice.status = 'processing'
        invoice.save(update_fields=['status'])

        return Response(
            {
                'invoice_id': str(invoice.id),
                'status': 'processing',
                'task_name': task_name,
                'message': 'Invoice uploaded successfully. OCR extraction is in progress.',
            },
            status=status.HTTP_202_ACCEPTED,
        )


class InvoiceListView(generics.ListAPIView):
    """GET /api/v1/invoices/"""
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [IsStaffOrManager]
    queryset = PurchaseInvoice.objects.order_by('-created_at')


class InvoiceDetailView(generics.RetrieveAPIView):
    """GET /api/v1/invoices/<uuid:id>/"""
    serializer_class = PurchaseInvoiceSerializer
    permission_classes = [IsStaffOrManager]
    queryset = PurchaseInvoice.objects.prefetch_related('line_items')
    lookup_field = 'id'

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data

        # Append a short-lived signed GCS URL for the HITL admin UI
        try:
            data['signed_url'] = generate_signed_url(instance.gcs_object_path, expiry_minutes=30)
        except Exception:
            data['signed_url'] = None   # Signed URL generation is non-critical

        return Response(data)
```

---

### `inventory/urls.py` — Add invoice routes

```python
from inventory.invoice_views import InvoiceUploadView, InvoiceListView, InvoiceDetailView

urlpatterns += [
    path('invoices/upload/', InvoiceUploadView.as_view(), name='invoice-upload'),
    path('invoices/', InvoiceListView.as_view(), name='invoice-list'),
    path('invoices/<uuid:id>/', InvoiceDetailView.as_view(), name='invoice-detail'),
]
```

---

### `pyproject.toml` — Add dependencies

```toml
"google-cloud-tasks>=2.16.0",
"google-cloud-storage>=2.16.0",
```

(Already listed in Spec 01 — confirm versions are pinned.)

---

### Environment Variables Required

| Variable | Description |
|---|---|
| `GCS_INVOICE_BUCKET` | Name of the private GCS bucket (without gs:// prefix) |
| `GCP_PROJECT_ID` | GCP project ID for Cloud Tasks queue path |
| `GCP_TASKS_LOCATION` | Cloud Tasks region (e.g., `asia-south1`) |
| `GCP_TASKS_QUEUE` | Queue name (e.g., `invoice-processing-queue`) |
| `TASKS_WORKER_URL` | Base URL of the Django Cloud Run service |
| `TASKS_SERVICE_ACCOUNT_EMAIL` | Service account for OIDC authentication to worker |

---

## Acceptance Criteria

- [ ] `POST /api/v1/invoices/upload/` with a valid JPEG returns HTTP 202
- [ ] The GCS object path is stored in the database record
- [ ] `PurchaseInvoice.status` changes from `pending` to `processing` after upload
- [ ] A Cloud Tasks task is created in the queue (verify via GCP console or mock)
- [ ] Unsupported file type returns 415
- [ ] File over 20 MB returns 413
- [ ] Customer JWT returns 403
- [ ] `GET /api/v1/invoices/<id>/` returns a `signed_url` field for the HITL UI
- [ ] GCS upload and Cloud Tasks dispatch are injected as mockable service functions (no direct SDK calls in the view)
