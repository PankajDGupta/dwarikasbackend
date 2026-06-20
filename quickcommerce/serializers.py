from rest_framework import serializers
from quickcommerce.models import QCPlatformListing, QCPurchaseOrder, QCPOLineItem

class ListingSyncTriggerSerializer(serializers.Serializer):
    product_id = serializers.UUIDField(required=True)
    platforms = serializers.ListField(
        child=serializers.ChoiceField(choices=['jiomart', 'blinkit']),
        required=True
    )
    fssai_license = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    marketplace_config = serializers.DictField(required=False, default=dict)


class QCListingStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = QCPlatformListing
        fields = [
            'platform',
            'platform_sku',
            'platform_upc',
            'sync_status',
            'trace_id',
            'submission_guid',
            'last_synced_at',
            'validation_issues'
        ]


class BlinkitPoItemSerializer(serializers.Serializer):
    sku = serializers.CharField(required=True)
    upc = serializers.CharField(required=True)
    ordered_quantity = serializers.IntegerField(min_value=1, required=True)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)
    mrp = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)


class BlinkitPoWebhookSerializer(serializers.Serializer):
    event = serializers.CharField(required=True)
    po_id = serializers.CharField(required=True)
    vendor_id = serializers.CharField(required=True)
    items = serializers.ListField(child=BlinkitPoItemSerializer(), required=True)
    delivery_pincode = serializers.CharField(required=True)
    expected_delivery_date = serializers.DateField(required=True)


class BlinkitAsnItemSerializer(serializers.Serializer):
    sku = serializers.CharField(required=True)
    dispatched_quantity = serializers.IntegerField(min_value=0, required=True)
    batch_number = serializers.CharField(required=False, allow_blank=True)


class BlinkitAsnSubmitSerializer(serializers.Serializer):
    po_id = serializers.CharField(required=True)
    dispatched_items = serializers.ListField(child=BlinkitAsnItemSerializer(), required=True)
    dispatch_date = serializers.DateTimeField(required=False)
    tracking_reference = serializers.CharField(required=False, allow_blank=True)


class JioMartOrderWebhookSerializer(serializers.Serializer):
    order_id = serializers.UUIDField(required=True)
    location_id = serializers.CharField(required=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=True)


class JioMartManifestCloseSerializer(serializers.Serializer):
    jiomart_order_id = serializers.UUIDField(required=True)
    manifest_id = serializers.CharField(required=True)


class OperationalMetricsQuerySerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=['blinkit', 'jiomart'], required=False)
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)
