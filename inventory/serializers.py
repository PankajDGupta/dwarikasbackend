from rest_framework import serializers
from inventory.models import Product, ProductVariant


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = [
            'id', 'sku', 'barcode', 'size', 'color',
            'stock_quantity', 'retail_price', 'mrp',
            'weight_volume', 'net_quantity', 'unit_of_measure',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'hsn_code', 'gst_slab', 'brand',
            'category', 'subcategory', 'description', 'image_url',
            'dietary_type', 'created_at', 'variants',
        ]
        read_only_fields = ['id', 'created_at']


class ProductWriteSerializer(serializers.ModelSerializer):
    """Slim write serializer — excludes nested variants to prevent accidental bulk writes."""
    class Meta:
        model = Product
        fields = [
            'name', 'hsn_code', 'gst_slab', 'brand',
            'category', 'subcategory', 'description', 'image_url',
            'dietary_type',
        ]
