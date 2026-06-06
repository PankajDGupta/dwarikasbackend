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

        # Upload to GCS (or local file fallback)
        gcs_path = upload_invoice_to_gcs(
            file_obj=uploaded_file,
            filename=uploaded_file.name,
            content_type=uploaded_file.content_type,
        )

        # Create invoice record in database
        try:
            uploader_id = uuid.UUID(request.user.username)
        except (ValueError, TypeError):
            uploader_id = None

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
