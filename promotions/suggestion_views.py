import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsManager
from promotions.models import DiscountSuggestion, Promotion, PromotionItem
from promotions.serializers import DiscountSuggestionSerializer


class DiscountSuggestionListView(generics.ListAPIView):
    """
    GET /api/v1/promotions/suggestions/
    Lists all pending discount suggestions ordered by score descending.
    Supports ?priority=critical|high|medium filter.
    """
    serializer_class = DiscountSuggestionSerializer
    permission_classes = [IsManager]

    def get_queryset(self):
        qs = DiscountSuggestion.objects.filter(
            status='pending',
        ).select_related('variant__product')

        priority = self.request.query_params.get('priority')
        if priority in ('critical', 'high', 'medium'):
            qs = qs.filter(priority=priority)

        return qs.order_by('-discount_score', '-analysed_at')


class DiscountSuggestionDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/promotions/suggestions/<uuid:id>/
    """
    serializer_class = DiscountSuggestionSerializer
    permission_classes = [IsManager]
    queryset = DiscountSuggestion.objects.select_related('variant__product').all()
    lookup_field = 'id'


class ApproveSuggestionView(APIView):
    """
    POST /api/v1/promotions/suggestions/<uuid:id>/approve/

    One-click promotion creation from a suggestion.
    Creates a Promotion and PromotionItem using the suggested discount config,
    then marks the suggestion as 'approved'.

    Optional request body to override defaults:
    {
        "title": "Custom title (optional)",
        "ends_days": 7,             // override suggested duration
        "discount_value": 15.00     // override suggested discount
    }
    """
    permission_classes = [IsManager]

    def post(self, request, id):
        try:
            suggestion = DiscountSuggestion.objects.select_related(
                'variant__product'
            ).get(id=id, status='pending')
        except DiscountSuggestion.DoesNotExist:
            return Response(
                {'error': 'Suggestion not found or already actioned.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Parse overrides safely
        try:
            if 'ends_days' in request.data:
                ends_days = int(request.data['ends_days'])
                if ends_days <= 0:
                    raise ValueError
            else:
                ends_days = suggestion.suggested_ends_days
        except (ValueError, TypeError):
            return Response({'error': 'ends_days must be a positive integer.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if 'discount_value' in request.data:
                discount_value = Decimal(str(request.data['discount_value']))
                if discount_value <= 0:
                    raise ValueError
            else:
                discount_value = suggestion.suggested_discount_value
        except (ValueError, TypeError, InvalidOperation):
            return Response({'error': 'discount_value must be a positive decimal number.'}, status=status.HTTP_400_BAD_REQUEST)

        manager_id = uuid.UUID(request.user.username)
        now = timezone.now()

        title = request.data.get(
            'title',
            f"{suggestion.variant.product.name} — {suggestion.priority.title()} Discount Offer"
        )

        with transaction.atomic():
            promotion = Promotion.objects.create(
                title=title,
                description=suggestion.reason_summary,
                discount_type=suggestion.suggested_discount_type,
                discount_value=discount_value,
                starts_at=now,
                ends_at=now + timedelta(days=ends_days),
                is_active=True,
                created_by=manager_id,
            )
            PromotionItem.objects.create(
                promotion=promotion,
                variant_id=suggestion.variant_id,
            )

            suggestion.status = 'approved'
            suggestion.approved_promotion = promotion
            suggestion.save(update_fields=['status', 'approved_promotion_id'])

        return Response({
            'suggestion_id': str(suggestion.id),
            'promotion_id': str(promotion.id),
            'title': promotion.title,
            'discount_type': promotion.discount_type,
            'discount_value': str(promotion.discount_value),
            'starts_at': promotion.starts_at.isoformat(),
            'ends_at': promotion.ends_at.isoformat(),
            'message': 'Promotion is now live.',
        }, status=status.HTTP_201_CREATED)


class DismissSuggestionView(APIView):
    """
    POST /api/v1/promotions/suggestions/<uuid:id>/dismiss/
    Dismisses a suggestion — it will not resurface for 30 days.
    """
    permission_classes = [IsManager]

    def post(self, request, id):
        try:
            suggestion = DiscountSuggestion.objects.get(id=id, status='pending')
        except DiscountSuggestion.DoesNotExist:
            return Response(
                {'error': 'Suggestion not found or already actioned.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        snooze_days = 30
        try:
            if 'snooze_days' in request.data:
                snooze_days = int(request.data['snooze_days'])
                if snooze_days < 0:
                    raise ValueError
        except (ValueError, TypeError):
            return Response({'error': 'snooze_days must be a non-negative integer.'}, status=status.HTTP_400_BAD_REQUEST)

        suggestion.status = 'dismissed'
        suggestion.dismissed_until = timezone.now() + timedelta(days=snooze_days)
        suggestion.save(update_fields=['status', 'dismissed_until'])

        return Response({'message': f'Suggestion dismissed for {snooze_days} days.'})
