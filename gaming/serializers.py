from rest_framework import serializers
from gaming.models import RewardTier, GameReward
from coupons.serializers import CouponSerializer


class RecordPlaySerializer(serializers.Serializer):
    game_session_id = serializers.CharField(max_length=255)
    game_type = serializers.CharField(max_length=100)
    won = serializers.BooleanField()
    win_level = serializers.CharField(max_length=100, required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get('won') and not attrs.get('win_level'):
            raise serializers.ValidationError(
                {'win_level': 'win_level is required when won=true.'}
            )
        return attrs


class GrantAdPlaySerializer(serializers.Serializer):
    ad_placement_id = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    ad_unit_id = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)


class RewardTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = RewardTier
        fields = [
            'id', 'name', 'game_type', 'win_level', 'discount_type',
            'discount_value', 'max_discount_cap', 'valid_days', 'description',
            'notify_whatsapp', 'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class GameRewardSerializer(serializers.ModelSerializer):
    coupon = CouponSerializer(read_only=True)
    reward_tier_name = serializers.CharField(source='reward_tier.name', read_only=True)

    class Meta:
        model = GameReward
        fields = [
            'id', 'game_type', 'win_level', 'reward_tier_name',
            'coupon', 'whatsapp_sent', 'whatsapp_delivered', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']
