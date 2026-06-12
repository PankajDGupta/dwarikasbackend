from rest_framework import serializers
from coupons.models import Coupon, CouponRedemption


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            'id', 'code', 'description', 'discount_type', 'discount_value',
            'max_discount_cap', 'min_order_value', 'max_uses', 'uses_per_user',
            'specific_user_id', 'is_active', 'valid_from', 'valid_until',
            'created_by', 'created_at', 'source'
        ]
        read_only_fields = ['id', 'created_by', 'created_at']


class CouponRedemptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CouponRedemption
        fields = [
            'id', 'coupon', 'user_id', 'order', 'reservation',
            'discount_applied', 'redeemed_at'
        ]
        read_only_fields = ['id', 'redeemed_at']
