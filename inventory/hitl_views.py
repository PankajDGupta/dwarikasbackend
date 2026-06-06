import uuid
from django.db import transaction, models
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics

from api.permissions import IsStaffOrManager
from inventory.models import PurchaseInvoice, InvoiceLineItem, ProductVariant
from inventory.serializers import PurchaseInvoiceSerializer, InvoiceLineItemSerializer
from inventory.gcs_service import generate_signed_url

CONFIRMABLE_STATUSES = {'review', 'confirmed'}


class InvoiceReviewView(APIView):
    """
    GET /api/v1/invoices/<uuid:id>/review/
    Returns full invoice detail with extracted line items and a signed URL
    for the HITL admin UI to render the original invoice image.
    """
    permission_classes = [IsStaffOrManager]

    def get(self, request, id):
        try:
            invoice = PurchaseInvoice.objects.prefetch_related('line_items').get(id=id)
        except PurchaseInvoice.DoesNotExist:
            return Response({'error': 'Invoice not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = PurchaseInvoiceSerializer(invoice)
        data = serializer.data

        # Append a 30-minute signed URL for the admin UI to render the original scan
        try:
            data['signed_image_url'] = generate_signed_url(invoice.gcs_object_path, expiry_minutes=30)
        except Exception:
            data['signed_image_url'] = None

        # Surface summary stats for the UI
        line_items = invoice.line_items.all()
        data['review_summary'] = {
            'total_items': line_items.count(),
            'needs_review_count': line_items.filter(needs_review=True).count(),
        }

        return Response(data)


class LineItemUpdateView(generics.UpdateAPIView):
    """
    PATCH /api/v1/invoices/line-items/<uuid:id>/
    Allows staff to correct an extracted line item (e.g., fix a misread SKU or quantity).
    After correction, the item's needs_review flag is cleared.
    """
    permission_classes = [IsStaffOrManager]
    serializer_class = InvoiceLineItemSerializer
    queryset = InvoiceLineItem.objects.all()
    lookup_field = 'id'
    http_method_names = ['patch']

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()

        # Do not allow edits on a confirmed invoice
        if instance.invoice.status == 'confirmed':
            return Response(
                {'error': 'Cannot edit line items on a confirmed invoice.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(needs_review=False)   # Staff correction clears the review flag
        return Response(serializer.data)


class InvoiceConfirmView(APIView):
    """
    POST /api/v1/invoices/<uuid:id>/confirm/
    Commits all line item quantities to the matching product_variants.stock_quantity.
    Matching is done by SKU. Unmatched SKUs are logged but do not block confirmation.
    """
    permission_classes = [IsStaffOrManager]

    def post(self, request, id):
        try:
            invoice = PurchaseInvoice.objects.prefetch_related('line_items').get(id=id)
        except PurchaseInvoice.DoesNotExist:
            return Response({'error': 'Invoice not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Status guard
        if invoice.status not in CONFIRMABLE_STATUSES:
            return Response(
                {
                    'error': f"Invoice cannot be confirmed in status '{invoice.status}'. "
                             f"Must be one of: {', '.join(CONFIRMABLE_STATUSES)}."
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Idempotency: already confirmed — return success without re-applying stock
        if invoice.status == 'confirmed':
            return Response(
                {'message': 'Invoice was already confirmed. No stock changes applied.'},
                status=status.HTTP_200_OK,
            )

        # Block confirmation if any items still need review
        unresolved = invoice.line_items.filter(needs_review=True).count()
        if unresolved > 0:
            return Response(
                {
                    'error': f'{unresolved} line item(s) still require review before confirmation.',
                    'action': 'Correct flagged items using PATCH /api/v1/invoices/line-items/<id>/ first.',
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        matched_skus = []
        unmatched_skus = []

        with transaction.atomic():
            for item in invoice.line_items.all():
                if not item.sku or item.quantity is None:
                    unmatched_skus.append({'sku': item.sku, 'reason': 'Missing SKU or quantity'})
                    continue

                updated = ProductVariant.objects.filter(sku=item.sku).update(
                    stock_quantity=models.F('stock_quantity') + item.quantity
                )

                if updated == 0:
                    unmatched_skus.append({'sku': item.sku, 'reason': 'SKU not found in product_variants'})
                else:
                    matched_skus.append(item.sku)

            invoice.status = 'confirmed'
            invoice.save(update_fields=['status'])

        return Response(
            {
                'invoice_id': str(invoice.id),
                'status': 'confirmed',
                'stock_updates_applied': len(matched_skus),
                'matched_skus': matched_skus,
                'unmatched_skus': unmatched_skus,
            },
            status=status.HTTP_200_OK,
        )
