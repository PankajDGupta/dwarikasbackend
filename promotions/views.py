import uuid
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from api.permissions import IsManager
from promotions.models import Promotion, PromotionItem
from promotions.serializers import PromotionSerializer, PromotionItemSerializer


class PromotionListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/promotions/  — public list (storefront)
    POST /api/v1/promotions/  — manager creates promotion
    """
    serializer_class = PromotionSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsManager()]

    def get_queryset(self):
        user = self.request.user
        role = getattr(user, 'role', 'customer') if user and user.is_authenticated else 'customer'

        if role in ('staff', 'manager'):
            return Promotion.objects.prefetch_related('items').order_by('-created_at')

        # Public GET returns only active, not-expired promotions
        now = timezone.now()
        from django.db import models
        return Promotion.objects.filter(
            is_active=True,
            starts_at__lte=now,
        ).filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now)
        ).prefetch_related('items').order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(created_by=uuid.UUID(self.request.user.username))


class PromotionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/promotions/<uuid:id>/  — public detail
    PATCH  /api/v1/promotions/<uuid:id>/  — manager update
    DELETE /api/v1/promotions/<uuid:id>/  — manager delete
    """
    serializer_class = PromotionSerializer
    lookup_field = 'id'
    queryset = Promotion.objects.prefetch_related('items').all()

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsManager()]


class ActivePromotionsView(generics.ListAPIView):
    """
    GET /api/v1/promotions/active/
    Optimised endpoint for the storefront promotion banner carousel.
    Returns currently live promotions with item count preview.
    """
    serializer_class = PromotionSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        now = timezone.now()
        from django.db import models
        return Promotion.objects.filter(
            is_active=True,
            starts_at__lte=now,
        ).filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now)
        ).prefetch_related('items').order_by('ends_at')


class PromotionItemCreateView(APIView):
    """
    POST /api/v1/promotions/<uuid:promotion_id>/items/
    """
    permission_classes = [IsManager]

    def post(self, request, promotion_id):
        try:
            promotion = Promotion.objects.get(id=promotion_id)
        except Promotion.DoesNotExist:
            return Response({'error': 'Promotion not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = PromotionItemSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(promotion=promotion)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PromotionItemDeleteView(APIView):
    """
    DELETE /api/v1/promotions/<uuid:promotion_id>/items/<uuid:item_id>/
    """
    permission_classes = [IsManager]

    def delete(self, request, promotion_id, item_id):
        try:
            item = PromotionItem.objects.get(id=item_id, promotion_id=promotion_id)
        except PromotionItem.DoesNotExist:
            return Response({'error': 'Promotion item not found.'}, status=status.HTTP_404_NOT_FOUND)
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SharePromotionWhatsAppView(APIView):
    """
    POST /api/v1/promotions/<uuid:id>/share/whatsapp/

    Broadcasts the promotion as a WhatsApp CTA message to the provided phone numbers.
    """
    permission_classes = [IsManager]

    def post(self, request, id):
        try:
            promotion = Promotion.objects.get(id=id)
        except Promotion.DoesNotExist:
            return Response({'error': 'Promotion not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not promotion.is_currently_live():
            return Response(
                {'error': 'Promotion is not currently live. Activate it and check start/end dates before sharing.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        phone_numbers = request.data.get('phone_numbers', [])
        if not phone_numbers:
            return Response({'error': 'phone_numbers is required and must not be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(phone_numbers) > 100:
            return Response({'error': 'Maximum 100 phone numbers per broadcast call.'}, status=status.HTTP_400_BAD_REQUEST)

        message_override = request.data.get('message_override')
        store_url = request.data.get('store_url')
        sent_by = uuid.UUID(request.user.username)

        from promotions.whatsapp_service import broadcast_promotion_to_whatsapp
        broadcast_result = broadcast_promotion_to_whatsapp(
            promotion=promotion,
            phone_numbers=phone_numbers,
            sent_by=sent_by,
            message_override=message_override,
            store_url=store_url,
        )

        return Response({
            'promotion_id': str(promotion.id),
            **broadcast_result,
        })
