from rest_framework import serializers
from inventory.models import Product, ProductVariant, Reservation, Order, PurchaseInvoice, InvoiceLineItem


class ProductVariantSerializer(serializers.ModelSerializer):
    active_promotion = serializers.SerializerMethodField()

    def get_active_promotion(self, obj):
        from promotions.services import get_active_promotion_for_variant, build_active_promotions_cache
        context = self.context
        if context is None or not isinstance(context, dict):
            context = {}
            self.context = context
        if 'promotions_cache' not in context:
            context['promotions_cache'] = build_active_promotions_cache()
        return get_active_promotion_for_variant(obj, active_promotions_cache=context['promotions_cache'])

    class Meta:
        model = ProductVariant
        fields = [
            'id', 'sku', 'barcode', 'size', 'color',
            'stock_quantity', 'retail_price', 'mrp',
            'weight_volume', 'net_quantity', 'unit_of_measure',
            'created_at', 'active_promotion',
        ]
        read_only_fields = ['id', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    """
    Full read serializer — returned on GET requests.

    Includes all catalog metadata fields. Category-specific fields
    (dietary_type for grocery; material, gender_target, fit_type for apparel)
    are always present but will be null/default for non-applicable product types.
    Clients should read product_type to decide which fields to render.
    """
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            # Core identification
            'id', 'name', 'hsn_code', 'gst_slab',
            # Classification
            'product_type',
            # Common catalog metadata
            'brand', 'category', 'subcategory', 'description', 'image_url',
            # Grocery-specific
            'dietary_type',
            # Apparel-specific
            'material', 'gender_target', 'fit_type',
            # Timestamps & nested
            'created_at', 'variants',
        ]
        read_only_fields = ['id', 'created_at']


class ProductWriteSerializer(serializers.ModelSerializer):
    """
    Slim write serializer — used for POST (create) and PATCH (update).

    Excludes nested variants to prevent accidental bulk writes.
    All optional fields (dietary_type, material, gender_target, fit_type)
    are writable so staff can set product-type-specific metadata.
    """
    class Meta:
        model = Product
        fields = [
            # Core
            'name', 'hsn_code', 'gst_slab',
            # Classification
            'product_type',
            # Common catalog metadata
            'brand', 'category', 'subcategory', 'description', 'image_url',
            # Grocery-specific
            'dietary_type',
            # Apparel-specific
            'material', 'gender_target', 'fit_type',
        ]


class ReservationSerializer(serializers.ModelSerializer):
    """
    Read serializer for Reservation rows.

    Embeds the full ProductVariantSerializer so that consumers of the
    GET /api/v1/checkout/reserve/list/ endpoint receive complete SKU
    details (price, stock_quantity, etc.) alongside each active hold.
    """
    variant = ProductVariantSerializer(read_only=True)

    class Meta:
        model = Reservation
        fields = ['id', 'variant', 'reserved_quantity', 'expires_at', 'status', 'effective_price', 'promotion_id']
        read_only_fields = ['id', 'expires_at', 'status', 'effective_price', 'promotion_id']


class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = [
            'id', 'user_id', 'total_amount', 'gst_amount',
            'payment_method', 'payment_status', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = [
            'id', 'sku', 'description', 'quantity', 'unit_price',
            'gst_rate', 'confidence_score', 'needs_review',
        ]
        read_only_fields = ['id', 'confidence_score']


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    line_items = InvoiceLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            'id', 'invoice_number', 'vendor_name', 'vendor_gstin',
            'issued_at', 'gcs_object_path', 'status', 'uploaded_by',
            'created_at', 'line_items',
        ]
        read_only_fields = ['id', 'gcs_object_path', 'uploaded_by', 'created_at', 'status']


