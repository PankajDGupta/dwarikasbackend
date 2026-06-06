"""
Serializers for the Packaging Job workflow.
"""
from rest_framework import serializers

from inventory.models import PackagingJob, PackagingJobOutput, ProductVariant, Product


class PackagingOutputInputSerializer(serializers.Serializer):
    """
    Validates one output line inside the POST /packaging-jobs/ request.

    Either `sku` (existing variant) or `product_id` + `sku` (new variant) must be provided.
    If the SKU does not exist under the given product, a new ProductVariant is auto-created.
    """
    product_id        = serializers.UUIDField(
        help_text="UUID of the parent Product this packet belongs to.",
    )
    sku               = serializers.CharField(
        max_length=100,
        help_text="SKU for this packet size. If not found, a new variant is auto-created.",
    )
    weight_per_packet = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False, allow_null=True,
        help_text="Net weight/volume per packet (e.g. 1.000 for a 1 kg pack).",
    )
    unit_of_measure   = serializers.ChoiceField(
        choices=['unit', 'kg', 'g', 'litre', 'ml'],
        default='unit',
    )
    packets_produced  = serializers.IntegerField(
        min_value=1,
        help_text="Number of packets produced at this size.",
    )
    retail_price      = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Retail selling price for one packet of this size.",
    )


class PackagingJobCreateSerializer(serializers.Serializer):
    """
    Validates the full POST /packaging-jobs/ request body.
    """
    source_description = serializers.CharField(
        help_text="Free-text description of the bulk input used "
                  "(e.g. '50kg Basmati Rice — Lal Qila INV-2026-001').",
    )
    source_variant_id  = serializers.UUIDField(
        required=False, allow_null=True,
        help_text="Optional UUID of the bulk ProductVariant that was consumed.",
    )
    bulk_quantity_used = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False, allow_null=True,
        help_text="Total bulk quantity consumed (e.g. 50 for 50 kg).",
    )
    bulk_unit          = serializers.ChoiceField(
        choices=['unit', 'kg', 'g', 'litre', 'ml'],
        required=False, allow_null=True,
    )
    notes              = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    outputs            = PackagingOutputInputSerializer(many=True, min_length=1)


class PackagingJobOutputResponseSerializer(serializers.ModelSerializer):
    """
    Serializes one output line in the response, including the barcode image URL.
    """
    variant_id        = serializers.UUIDField(source='variant.id', read_only=True)
    sku               = serializers.CharField(source='variant.sku', read_only=True)
    retail_price      = serializers.DecimalField(
        source='variant.retail_price',
        max_digits=12, decimal_places=2, read_only=True,
    )
    barcode_image_url = serializers.SerializerMethodField()

    class Meta:
        model  = PackagingJobOutput
        fields = [
            'id', 'variant_id', 'sku', 'packets_produced',
            'weight_per_packet', 'unit_of_measure',
            'barcode_value', 'barcode_image_url',
            'retail_price', 'is_new_variant',
        ]

    def get_barcode_image_url(self, obj):
        if obj.barcode_value:
            request = self.context.get('request')
            path = f'/api/v1/barcodes/{obj.variant.sku}/code128/'
            if request:
                return request.build_absolute_uri(path)
            return path
        return None


class PackagingJobResponseSerializer(serializers.ModelSerializer):
    """
    Full response shape for a PackagingJob including all output lines.
    """
    outputs = PackagingJobOutputResponseSerializer(many=True, read_only=True)

    class Meta:
        model  = PackagingJob
        fields = [
            'id', 'source_description', 'source_variant_id',
            'bulk_quantity_used', 'bulk_unit',
            'notes', 'created_by', 'created_at',
            'outputs',
        ]
