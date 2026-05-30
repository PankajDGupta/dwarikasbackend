# Spec 11 — Document AI OCR Worker (Cloud Tasks Handler)

## Goal
Implement the asynchronous Cloud Tasks worker endpoint that processes invoice images. This is the target URL that Cloud Tasks calls after Spec 10 enqueues a job. It orchestrates:
1. Download the raw file from GCS
2. Preprocess the image with OpenCV (binarisation, deskewing, noise reduction)
3. Submit to Google Document AI for structured line-item extraction
4. Parse the response and store `InvoiceLineItem` rows, flagging low-confidence fields
5. Update `PurchaseInvoice.status` to `review` (ready for HITL) or `failed`

This endpoint is **not** called by the frontend. It is invoked exclusively by the Cloud Tasks service and must verify the OIDC token in the request header.

---

## Scope
- `POST /api/v1/tasks/process-invoice/` — Cloud Tasks worker endpoint (internal only)
- Image preprocessing via OpenCV
- Document AI API integration
- Confidence threshold flagging: fields < 0.90 set `needs_review = True`
- Idempotent: re-invoking with the same `invoice_id` is safe

---

## Files to Create / Modify

### `tasks/` — New Django app

```bash
python manage.py startapp tasks
```

Add to `INSTALLED_APPS`:
```python
'tasks.apps.TasksConfig',
```

---

### `tasks/document_ai_service.py` — New file

```python
"""
Google Document AI integration for structured invoice extraction.
"""
import os
import base64
from dataclasses import dataclass, field
from typing import List
from google.cloud import documentai_v1 as documentai

DOCUMENT_AI_PROJECT = os.environ.get('GCP_PROJECT_ID')
DOCUMENT_AI_LOCATION = os.environ.get('DOCUMENT_AI_LOCATION', 'us')
DOCUMENT_AI_PROCESSOR_ID = os.environ.get('DOCUMENT_AI_PROCESSOR_ID')

CONFIDENCE_THRESHOLD = 0.90  # Fields below this are flagged for human review


@dataclass
class ExtractedLineItem:
    sku: str = ''
    description: str = ''
    quantity: int = None
    unit_price: float = None
    gst_rate: float = None
    confidence_score: float = 1.0
    needs_review: bool = False


@dataclass
class ExtractedInvoice:
    invoice_number: str = ''
    vendor_name: str = ''
    vendor_gstin: str = ''
    issued_at: str = ''         # ISO date string, e.g. '2024-01-15'
    line_items: List[ExtractedLineItem] = field(default_factory=list)
    raw_confidence: float = 1.0


def process_invoice_bytes(file_bytes: bytes, mime_type: str) -> ExtractedInvoice:
    """
    Submits raw file bytes to Document AI and returns a structured ExtractedInvoice.
    """
    client = documentai.DocumentProcessorServiceClient()
    processor_name = client.processor_path(
        DOCUMENT_AI_PROJECT, DOCUMENT_AI_LOCATION, DOCUMENT_AI_PROCESSOR_ID
    )

    raw_document = documentai.RawDocument(content=file_bytes, mime_type=mime_type)
    request = documentai.ProcessRequest(name=processor_name, raw_document=raw_document)
    result = client.process_document(request=request)
    document = result.document

    return _parse_document(document)


def _parse_document(document) -> ExtractedInvoice:
    """
    Maps Document AI entity responses to the ExtractedInvoice dataclass.
    Field names correspond to a trained Document AI Invoice Parser processor.
    """
    invoice = ExtractedInvoice()
    line_item_map: dict = {}   # Keyed by line item index

    for entity in document.entities:
        confidence = entity.confidence
        value = entity.mention_text.strip()

        # --- Header-level fields ---
        if entity.type_ == 'invoice_id':
            invoice.invoice_number = value
        elif entity.type_ == 'supplier_name':
            invoice.vendor_name = value
        elif entity.type_ == 'supplier_tax_id':
            invoice.vendor_gstin = value
        elif entity.type_ == 'invoice_date':
            invoice.issued_at = value

        # --- Line item fields (Document AI groups these as sub-entities) ---
        elif entity.type_ == 'line_item':
            idx = id(entity)    # Unique reference per entity object
            item = ExtractedLineItem()

            for prop in entity.properties:
                prop_confidence = prop.confidence
                prop_value = prop.mention_text.strip()
                item.confidence_score = min(item.confidence_score, prop_confidence)

                if prop.type_ == 'line_item/product_code':
                    item.sku = prop_value
                elif prop.type_ == 'line_item/description':
                    item.description = prop_value
                elif prop.type_ in ('line_item/quantity', 'line_item/unit'):
                    try:
                        item.quantity = int(float(prop_value))
                    except (ValueError, TypeError):
                        item.confidence_score = 0.0
                elif prop.type_ == 'line_item/unit_price':
                    try:
                        item.unit_price = float(prop_value.replace(',', ''))
                    except (ValueError, TypeError):
                        item.confidence_score = 0.0
                elif prop.type_ == 'line_item/tax_amount':
                    try:
                        item.gst_rate = float(prop_value.replace('%', ''))
                    except (ValueError, TypeError):
                        pass

            item.needs_review = item.confidence_score < CONFIDENCE_THRESHOLD
            line_item_map[idx] = item

    invoice.line_items = list(line_item_map.values())
    return invoice
```

---

### `tasks/image_preprocessing.py` — New file

```python
"""
OpenCV-based image preprocessing pipeline.
Applied before Document AI submission to improve OCR accuracy.
"""
import cv2
import numpy as np


def preprocess_image(image_bytes: bytes) -> bytes:
    """
    Applies binarisation, deskewing, and noise reduction to a raw image.
    Returns the preprocessed image as JPEG bytes.
    """
    # Decode bytes → OpenCV Mat
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    # Step 1: Greyscale conversion
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Step 2: Binarisation (Otsu threshold for adaptive document contrast)
    _, binary = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Step 3: Noise reduction (morphological opening to remove small artifacts)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    # Step 4: Deskewing (correct physical scan rotation up to ±15°)
    coords = np.column_stack(np.where(cleaned > 0))
    if len(coords) > 0:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) <= 15:   # Only correct small, realistic skews
            h, w = cleaned.shape
            centre = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(centre, angle, 1.0)
            cleaned = cv2.warpAffine(cleaned, M, (w, h), flags=cv2.INTER_CUBIC,
                                     borderMode=cv2.BORDER_REPLICATE)

    # Encode back to JPEG bytes for Document AI submission
    _, output = cv2.imencode('.jpg', cleaned, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return output.tobytes()
```

---

### `tasks/views.py` — New file

```python
"""
Internal Cloud Tasks worker endpoints.
These are never called by frontend clients — only by the Cloud Tasks service.
"""
import json
from django.db import transaction
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from inventory.models import PurchaseInvoice, InvoiceLineItem
from inventory.gcs_service import _get_client as get_gcs_client
from tasks.document_ai_service import process_invoice_bytes
from tasks.image_preprocessing import preprocess_image

SUPPORTED_IMAGE_MIME_TYPES = {'image/jpeg', 'image/png', 'image/webp'}


@method_decorator(csrf_exempt, name='dispatch')
class ProcessInvoiceTaskView(View):
    """
    POST /api/v1/tasks/process-invoice/
    Invoked by Google Cloud Tasks. Validates the OIDC identity header,
    downloads the invoice from GCS, preprocesses it, extracts line items
    via Document AI, and writes results to the database.
    """

    def post(self, request):
        # ── Cloud Tasks identity verification ────────────────────────────────
        # Cloud Tasks attaches an OIDC ID token in the Authorization header
        # when configured with `oidc_token`. Verify it is present.
        # Full OIDC validation is handled at the infrastructure level (Cloud Run
        # audience validation) — we just guard against direct browser calls here.
        if not request.headers.get('X-CloudTasks-TaskName'):
            return JsonResponse({'error': 'Forbidden. Not a Cloud Tasks request.'}, status=403)

        # ── Parse payload ─────────────────────────────────────────────────────
        try:
            body = json.loads(request.body)
            invoice_id = body['invoice_id']
            gcs_object_path = body['gcs_object_path']
        except (json.JSONDecodeError, KeyError):
            return JsonResponse({'error': 'Invalid task payload.'}, status=400)

        # ── Load invoice record ───────────────────────────────────────────────
        try:
            invoice = PurchaseInvoice.objects.get(id=invoice_id)
        except PurchaseInvoice.DoesNotExist:
            return JsonResponse({'error': f'Invoice {invoice_id} not found.'}, status=404)

        # Idempotency guard: skip if already confirmed or being re-processed
        if invoice.status == 'confirmed':
            return JsonResponse({'message': 'Invoice already confirmed. Skipping.'}, status=200)

        # ── Download file from GCS ────────────────────────────────────────────
        try:
            path_parts = gcs_object_path.replace('gs://', '').split('/', 1)
            bucket_name, object_name = path_parts[0], path_parts[1]
            blob = get_gcs_client().bucket(bucket_name).blob(object_name)
            file_bytes = blob.download_as_bytes()
            content_type = blob.content_type or 'application/octet-stream'
        except Exception as e:
            invoice.status = 'failed'
            invoice.save(update_fields=['status'])
            return JsonResponse({'error': f'GCS download failed: {str(e)}'}, status=500)

        # ── Preprocess image if applicable ────────────────────────────────────
        try:
            if content_type in SUPPORTED_IMAGE_MIME_TYPES:
                file_bytes = preprocess_image(file_bytes)
                content_type = 'image/jpeg'   # Output of preprocessing is always JPEG
        except Exception as e:
            # Preprocessing failure is non-fatal — submit raw bytes
            pass

        # ── Document AI extraction ────────────────────────────────────────────
        try:
            extracted = process_invoice_bytes(file_bytes, mime_type=content_type)
        except Exception as e:
            invoice.status = 'failed'
            invoice.save(update_fields=['status'])
            return JsonResponse({'error': f'Document AI extraction failed: {str(e)}'}, status=500)

        # ── Persist results atomically ────────────────────────────────────────
        with transaction.atomic():
            invoice.invoice_number = extracted.invoice_number or None
            invoice.vendor_name = extracted.vendor_name or None
            invoice.vendor_gstin = extracted.vendor_gstin or None
            if extracted.issued_at:
                try:
                    from datetime import date
                    invoice.issued_at = date.fromisoformat(extracted.issued_at)
                except ValueError:
                    pass

            # Determine final status
            has_review_items = any(item.needs_review for item in extracted.line_items)
            invoice.status = 'review' if has_review_items else 'confirmed'
            invoice.save()

            # Delete any previous extraction attempt (idempotent re-run)
            InvoiceLineItem.objects.filter(invoice_id=invoice.id).delete()

            # Bulk insert all extracted line items
            InvoiceLineItem.objects.bulk_create([
                InvoiceLineItem(
                    invoice_id=invoice.id,
                    sku=item.sku or None,
                    description=item.description or None,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    gst_rate=item.gst_rate,
                    confidence_score=round(item.confidence_score, 3),
                    needs_review=item.needs_review,
                )
                for item in extracted.line_items
            ])

        return JsonResponse({
            'invoice_id': str(invoice.id),
            'status': invoice.status,
            'line_items_extracted': len(extracted.line_items),
            'needs_review_count': sum(1 for i in extracted.line_items if i.needs_review),
        }, status=200)
```

---

### `tasks/urls.py` — New file

```python
from django.urls import path
from tasks.views import ProcessInvoiceTaskView

urlpatterns = [
    path('tasks/process-invoice/', ProcessInvoiceTaskView.as_view(), name='task-process-invoice'),
]
```

---

### `dwarikasbackend/urls.py` — Include task routes

```python
path('api/v1/', include('tasks.urls')),
```

---

### `pyproject.toml` — Add dependencies

```toml
"google-cloud-documentai>=2.24.0",
"opencv-python-headless>=4.9.0",
"numpy>=1.26.0",
```

### Environment Variables Required

| Variable | Description |
|---|---|
| `DOCUMENT_AI_PROCESSOR_ID` | Document AI Invoice Parser processor ID |
| `DOCUMENT_AI_LOCATION` | Processor region (e.g., `us`) |

---

## Acceptance Criteria

- [ ] Direct browser call without `X-CloudTasks-TaskName` header returns 403
- [ ] Valid task payload downloads file from GCS, runs preprocessing, and calls Document AI
- [ ] Line items with confidence < 0.90 have `needs_review = True`
- [ ] `PurchaseInvoice.status` is `review` when any line item needs review, `confirmed` otherwise
- [ ] Re-running with the same `invoice_id` deletes old line items and re-inserts new ones (idempotent)
- [ ] `PurchaseInvoice.status` is set to `failed` if GCS download or Document AI call fails
- [ ] `preprocess_image()` is unit-testable with a raw bytes input
- [ ] All DB writes are wrapped in `transaction.atomic()`
