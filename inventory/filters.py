import django_filters
from inventory.models import Product, ProductVariant


class ProductFilter(django_filters.FilterSet):
    """
    Filterset for the product list endpoint.

    Supports filtering by:
    - id            — exact UUID match (e.g. ?id=<uuid>)
    - hsn_code      — case-insensitive exact HSN code (e.g. ?hsn_code=6101)
    - gst_slab      — numeric GST rate (e.g. ?gst_slab=5)
    - product_type  — one of 'grocery', 'apparel', 'general' (e.g. ?product_type=apparel)
    - brand         — case-insensitive partial match (e.g. ?brand=levi)
    - category      — case-insensitive partial match (e.g. ?category=shirts)
    - gender_target — one of 'men', 'women', 'unisex', 'boys', 'girls', 'none'
    """
    id = django_filters.UUIDFilter()
    hsn_code = django_filters.CharFilter(lookup_expr='iexact')
    gst_slab = django_filters.NumberFilter()
    product_type = django_filters.CharFilter(lookup_expr='iexact')
    brand = django_filters.CharFilter(lookup_expr='icontains')
    category = django_filters.CharFilter(lookup_expr='icontains')
    gender_target = django_filters.CharFilter(lookup_expr='iexact')

    class Meta:
        model = Product
        fields = ['id', 'hsn_code', 'gst_slab', 'product_type', 'brand', 'category', 'gender_target']


class ProductVariantFilter(django_filters.FilterSet):
    """
    Filterset for the product variant list endpoint.

    Supports filtering by:
    - sku   — case-insensitive partial match (e.g. ?sku=DW-S)
    - color — case-insensitive exact match (e.g. ?color=blue)
    - size  — case-insensitive exact match (e.g. ?size=M or ?size=500ml)
    """
    sku = django_filters.CharFilter(lookup_expr='icontains')
    color = django_filters.CharFilter(lookup_expr='iexact')
    size = django_filters.CharFilter(lookup_expr='iexact')

    class Meta:
        model = ProductVariant
        fields = ['sku', 'color', 'size']
