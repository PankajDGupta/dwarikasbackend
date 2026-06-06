import hashlib
import secrets
from django.db import transaction
from django.db.models import F
from rest_framework import status, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from api.external_auth import ExternalApiKeyAuthentication, HasValidRequestSignature
from api.permissions import IsManager
from inventory.models import ProductVariant, Order, ExternalApiKey


class ExternalApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalApiKey
        fields = ['id', 'partner_name', 'is_active', 'created_at']
        read_only_fields = ['id', 'is_active', 'created_at']


class AdminApiKeyListCreateView(APIView):
    """
    GET /api/v1/admin/api-keys/
    Lists all external partner API keys. Restricted to Managers.

    POST /api/v1/admin/api-keys/
    Generates a new API key for a partner, hashes it, and returns the raw key ONCE.
    Restricted to Managers.
    """
    permission_classes = [IsManager]

    def get(self, request):
        keys = ExternalApiKey.objects.all().order_by('-created_at')
        serializer = ExternalApiKeySerializer(keys, many=True)
        return Response(serializer.data)

    def post(self, request):
        partner_name = request.data.get('partner_name')
        if not partner_name:
            return Response({'error': 'partner_name is required.'}, status=status.HTTP_400_BAD_REQUEST)

        raw_key = secrets.token_urlsafe(48)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        key = ExternalApiKey.objects.create(
            partner_name=partner_name,
            key_hash=key_hash,
            is_active=True
        )

        serializer = ExternalApiKeySerializer(key)
        data = serializer.data
        data['raw_key'] = raw_key  # Returned ONLY once here
        return Response(data, status=status.HTTP_201_CREATED)


class AdminApiKeyRevokeView(APIView):
    """
    POST /api/v1/admin/api-keys/<uuid:pk>/revoke/
    Revokes (deactivates) an active API key. Restricted to Managers.
    """
    permission_classes = [IsManager]

    def post(self, request, pk):
        try:
            key = ExternalApiKey.objects.get(pk=pk)
        except ExternalApiKey.DoesNotExist:
            return Response({'error': 'API key not found.'}, status=status.HTTP_404_NOT_FOUND)

        key.is_active = False
        key.save()
        return Response({'message': 'API key revoked successfully.'}, status=status.HTTP_200_OK)


class ExternalInventorySyncView(APIView):
    """
    POST /api/v1/external/inventory/sync/
    Applies a batch of quantity_delta adjustments to product_variants.stock_quantity.
    Used by ERP systems to reconcile physical warehouse counts.
    """
    authentication_classes = [HasValidRequestSignature, ExternalApiKeyAuthentication]

    def post(self, request):
        sync_items = request.data.get('sync_items', [])
        if not sync_items or not isinstance(sync_items, list):
            return Response({'error': 'sync_items array is required.'}, status=status.HTTP_400_BAD_REQUEST)

        results = {'applied': [], 'not_found': [], 'errors': []}

        with transaction.atomic():
            for item in sync_items:
                sku = item.get('sku')
                delta = item.get('quantity_delta')

                if not sku or delta is None:
                    results['errors'].append({'item': item, 'reason': 'Missing sku or quantity_delta'})
                    continue

                try:
                    delta = int(delta)
                except (TypeError, ValueError):
                    results['errors'].append({'item': item, 'reason': 'quantity_delta must be an integer'})
                    continue

                updated = ProductVariant.objects.filter(sku=sku).update(
                    stock_quantity=F('stock_quantity') + delta
                )
                if updated == 0:
                    results['not_found'].append(sku)
                else:
                    results['applied'].append({'sku': sku, 'delta': delta})

        return Response(results, status=status.HTTP_200_OK)


class ExternalShipmentUpdateView(APIView):
    """
    PATCH /api/v1/external/shipments/update/
    Updates the fulfillment status for an order from a logistics partner webhook.
    """
    authentication_classes = [HasValidRequestSignature, ExternalApiKeyAuthentication]
    VALID_STATUSES = {'staged', 'picked_up', 'in_transit', 'delivered'}

    def patch(self, request):
        order_id = request.data.get('order_id')
        carrier_status = request.data.get('carrier_status')
        tracking_reference = request.data.get('tracking_reference')

        if not all([order_id, carrier_status, tracking_reference]):
            return Response(
                {'error': 'order_id, carrier_status, and tracking_reference are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if carrier_status not in self.VALID_STATUSES:
            return Response(
                {'error': f"carrier_status must be one of: {', '.join(self.VALID_STATUSES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            import uuid
            order = Order.objects.get(id=uuid.UUID(str(order_id)))
        except (Order.DoesNotExist, ValueError):
            return Response({'error': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Update tracking fields and save to DB
        order.carrier_status = carrier_status
        order.tracking_reference = tracking_reference
        order.save(update_fields=['carrier_status', 'tracking_reference'])

        return Response(
            {
                'order_id': str(order.id),
                'carrier_status': carrier_status,
                'tracking_reference': tracking_reference,
                'message': 'Shipment status updated.',
            },
            status=status.HTTP_200_OK,
        )
