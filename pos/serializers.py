from rest_framework import serializers
from pos.models import PosCart, PosCartItem, OrderItem
from inventory.serializers import ProductVariantSerializer


class PosCartItemSerializer(serializers.ModelSerializer):
    variant = ProductVariantSerializer(read_only=True)
    variant_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = PosCartItem
        fields = ['id', 'variant', 'variant_id', 'quantity', 'unit_price', 'created_at']
        read_only_fields = ['id', 'unit_price', 'created_at']


class PosCartSerializer(serializers.ModelSerializer):
    items = PosCartItemSerializer(many=True, read_only=True)

    class Meta:
        model = PosCart
        fields = ['id', 'staff_user_id', 'customer_phone', 'status', 'items', 'created_at']
        read_only_fields = ['id', 'staff_user_id', 'status', 'created_at']


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            'id', 'sku_snapshot', 'product_name_snapshot', 'hsn_code_snapshot',
            'gst_slab_snapshot', 'quantity', 'unit_price', 'subtotal', 'gst_amount', 'line_total',
        ]
        read_only_fields = fields
