import uuid
import os
from django.utils import timezone
from datetime import timedelta, datetime
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsManager
from gaming.models import RewardTier, GameReward
from gaming.serializers import (
    RewardTierSerializer,
    GameRewardSerializer,
    RecordPlaySerializer,
    GrantAdPlaySerializer,
)
from gaming.services import record_play, grant_ad_play, get_plays_data, get_ad_status

# System user UUID used as coupon created_by attribute.
GAMING_SYSTEM_USER_ID = os.environ.get('GAMING_SYSTEM_USER_ID', '00000000-0000-0000-0000-000000000000')


class GamePlaysEarnedView(APIView):
    """
    GET /api/v1/gaming/earn/
    Returns plays earned (order based), plays used, ad watch plays counters,
    and total remaining plays.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = uuid.UUID(request.user.username)
        plays_data = get_plays_data(user_id)
        return Response(plays_data)


class RecordPlayView(APIView):
    """
    POST /api/v1/gaming/record-play/
    Consumes a play quota slot and issues a reward coupon if won=True.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RecordPlaySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user_id = uuid.UUID(request.user.username)
        system_user_id = uuid.UUID(GAMING_SYSTEM_USER_ID)

        try:
            result = record_play(
                user_id=user_id,
                game_session_id=serializer.validated_data['game_session_id'],
                game_type=serializer.validated_data['game_type'],
                won=serializer.validated_data['won'],
                win_level=serializer.validated_data.get('win_level'),
                system_user_id=system_user_id,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)

        response_data = {
            'play_id': str(result['play_id']),
            'plays_remaining': result['plays_remaining'],
            'won': result['won'],
            'coupon_code': result['coupon_code'],
        }

        if result['reward'] and result['reward'].coupon:
            coupon = result['reward'].coupon
            response_data.update({
                'coupon_discount_type': coupon.discount_type,
                'coupon_discount_value': str(coupon.discount_value),
                'coupon_valid_until': coupon.valid_until.date().isoformat() if coupon.valid_until else None,
            })

        return Response(response_data, status=status.HTTP_200_OK)


class AdStatusView(APIView):
    """
    GET /api/v1/gaming/ad-status/
    Returns whether the player can watch a rewarded ad today,
    current counters, and the next resets time.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = uuid.UUID(request.user.username)
        status_data = get_ad_status(user_id)
        return Response(status_data)


class GrantAdPlayView(APIView):
    """
    POST /api/v1/gaming/grant-ad-play/
    Called after watching an ad to grant one play. Enforces the daily cap of 5.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = GrantAdPlaySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user_id = uuid.UUID(request.user.username)
        try:
            result = grant_ad_play(
                user_id=user_id,
                ad_placement_id=serializer.validated_data.get('ad_placement_id'),
                ad_unit_id=serializer.validated_data.get('ad_unit_id'),
            )
        except ValueError as exc:
            now = timezone.now()
            today = now.date()
            next_day = today + timedelta(days=1)
            resets_at = timezone.make_aware(datetime.combine(next_day, datetime.min.time()))
            return Response({
                'granted': False,
                'detail': str(exc),
                'resets_at': resets_at.isoformat(),
            }, status=status.HTTP_403_FORBIDDEN)

        return Response(result, status=status.HTTP_200_OK)


class UserRewardListView(generics.ListAPIView):
    """
    GET /api/v1/gaming/rewards/
    Returns all gaming rewards (with coupon codes) earned by the calling user.
    """
    serializer_class = GameRewardSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user_id = uuid.UUID(self.request.user.username)
        return GameReward.objects.filter(user_id=user_id).select_related('coupon', 'reward_tier')


class UserRewardDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/gaming/rewards/<uuid:id>/
    Returns a single reward detail for the calling user.
    """
    serializer_class = GameRewardSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        user_id = uuid.UUID(self.request.user.username)
        return GameReward.objects.filter(user_id=user_id).select_related('coupon', 'reward_tier')


class RewardTierListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/gaming/reward-tiers/  — Manager only
    POST /api/v1/gaming/reward-tiers/  — Manager only
    """
    serializer_class = RewardTierSerializer
    permission_classes = [IsManager]
    queryset = RewardTier.objects.order_by('game_type', 'win_level')


class RewardTierDetailView(generics.RetrieveUpdateAPIView):
    """
    GET / PATCH /api/v1/gaming/reward-tiers/<uuid:id>/  — Manager only
    """
    serializer_class = RewardTierSerializer
    permission_classes = [IsManager]
    queryset = RewardTier.objects.all()
    lookup_field = 'id'
