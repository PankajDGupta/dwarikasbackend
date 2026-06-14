from rest_framework import serializers
from promotions.models import Promotion, PromotionItem, DiscountSuggestion


from inventory.models import Product, ProductVariant

class PromotionItemSerializer(serializers.ModelSerializer):
    product_id = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all(), source='product', required=False, allow_null=True
    )
    variant_id = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariant.objects.all(), source='variant', required=False, allow_null=True
    )

    class Meta:
        model = PromotionItem
        fields = ['id', 'product_id', 'variant_id', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, data):
        if not data.get('product') and not data.get('variant'):
            raise serializers.ValidationError(
                'At least one of product_id or variant_id must be provided.'
            )
        return data


class PromotionSerializer(serializers.ModelSerializer):
    items = PromotionItemSerializer(many=True, read_only=True)
    is_live = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = [
            'id', 'title', 'description', 'discount_type', 'discount_value',
            'max_discount_cap', 'min_order_value', 'banner_image_url',
            'starts_at', 'ends_at', 'is_active', 'is_live',
            'created_by', 'created_at', 'items',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'is_live']

    def get_is_live(self, obj):
        return obj.is_currently_live()


class SuggestionProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['name', 'category']


class SuggestionVariantSerializer(serializers.ModelSerializer):
    product = SuggestionProductSerializer(read_only=True)

    class Meta:
        model = ProductVariant
        fields = ['id', 'sku', 'retail_price', 'stock_quantity', 'product']


class DiscountSuggestionSerializer(serializers.ModelSerializer):
    variant = SuggestionVariantSerializer(read_only=True)

    class Meta:
        model = DiscountSuggestion
        fields = [
            'id', 'variant', 'discount_score', 'priority', 'reason_summary', 'reasons',
            'suggested_discount_type', 'suggested_discount_value', 'suggested_ends_days',
            'current_stock', 'avg_monthly_sales', 'days_since_last_order',
            'cost_price', 'margin_pct', 'status', 'dismissed_until',
            'approved_promotion', 'analysed_at', 'created_at',
        ]
        read_only_fields = [
            'id', 'variant', 'discount_score', 'priority', 'reason_summary', 'reasons',
            'suggested_discount_type', 'suggested_discount_value', 'suggested_ends_days',
            'current_stock', 'avg_monthly_sales', 'days_since_last_order',
            'cost_price', 'margin_pct', 'status', 'dismissed_until',
            'approved_promotion', 'analysed_at', 'created_at',
        ]

