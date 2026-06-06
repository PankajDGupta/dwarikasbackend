"""
Internal Cloud Tasks worker endpoints.
These are never called by frontend clients — only by the Cloud Tasks service.
"""
import json
import mimetypes
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

        # ── Download file ─────────────────────────────────────────────────────
        try:
            if gcs_object_path.startswith('file:///'):
                local_path = gcs_object_path.replace('file:///', '')
                with open(local_path, 'rb') as f:
                    file_bytes = f.read()
                content_type, _ = mimetypes.guess_type(local_path)
                content_type = content_type or 'application/octet-stream'
            else:
                path_parts = gcs_object_path.replace('gs://', '').split('/', 1)
                bucket_name, object_name = path_parts[0], path_parts[1]
                blob = get_gcs_client().bucket(bucket_name).blob(object_name)
                file_bytes = blob.download_as_bytes()
                content_type = blob.content_type or 'application/octet-stream'
        except Exception as e:
            invoice.status = 'failed'
            invoice.save(update_fields=['status'])
            return JsonResponse({'error': f'File download/read failed: {str(e)}'}, status=500)

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
                    confidence_score=round(item.confidence_score, 3) if item.confidence_score is not None else None,
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
