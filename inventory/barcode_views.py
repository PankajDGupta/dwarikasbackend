from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from api.permissions import IsStaffOrManager
from inventory.barcode_service import generate_code128, generate_ean13
from inventory.models import ProductVariant


class Code128BarcodeView(APIView):
    """
    GET /api/v1/barcodes/<str:sku>/code128/?batch_id=<optional>
    Returns a PNG image stream of a Code 128 barcode for internal inventory labelling.
    """
    permission_classes = [IsStaffOrManager]

    def get(self, request, sku):
        # Verify SKU exists in the database before generating
        if not ProductVariant.objects.filter(sku=sku).exists():
            return Response({'error': f"SKU '{sku}' not found."}, status=status.HTTP_404_NOT_FOUND)

        batch_id = request.query_params.get('batch_id', '')
        buffer = generate_code128(sku, batch_id=batch_id)

        response = HttpResponse(buffer.read(), content_type='image/png')
        response['Content-Disposition'] = f'inline; filename="{sku}-code128.png"'
        response['Cache-Control'] = 'no-store'   # Barcodes must always be freshly generated
        return response


class EAN13BarcodeView(APIView):
    """
    GET /api/v1/barcodes/<str:sku>/ean13/
    Returns a PNG image stream of an EAN-13 barcode for standard retail shelf labels.
    """
    permission_classes = [IsStaffOrManager]

    def get(self, request, sku):
        if not ProductVariant.objects.filter(sku=sku).exists():
            return Response({'error': f"SKU '{sku}' not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            buffer = generate_ean13(sku)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        response = HttpResponse(buffer.read(), content_type='image/png')
        response['Content-Disposition'] = f'inline; filename="{sku}-ean13.png"'
        response['Cache-Control'] = 'no-store'
        return response
