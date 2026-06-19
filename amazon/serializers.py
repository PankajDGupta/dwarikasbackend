from rest_framework import serializers
from inventory.models import Product
from .models import AmazonListing

class AmazonListingSyncSerializer(serializers.Serializer):
    product_id = serializers.UUIDField(required=True)
    marketplace_id = serializers.CharField(max_length=50, required=True)

    def validate_product_id(self, value):
        try:
            product = Product.objects.get(id=value)
        except Product.DoesNotExist:
            raise serializers.ValidationError("Product not found.")
            
        # Verify the product has at least one variant with a barcode
        variants = product.variants.all()
        if not variants.exists():
            raise serializers.ValidationError("Product has no variants.")
            
        if not any(v.barcode for v in variants):
            raise serializers.ValidationError("Product has no EAN/UPC barcode.")
            
        return value


class AmazonListingStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = AmazonListing
        fields = [
            'product_id',
            'sku',
            'asin',
            'sync_status',
            'last_synced_at',
            'validation_issues',  # maps to 'issues' in the API spec response
        ]
        
    def to_representation(self, instance):
        ret = super().to_representation(instance)
        # Rename validation_issues to issues for compliance with the polling API spec
        ret['issues'] = ret.pop('validation_issues', [])
        return ret
