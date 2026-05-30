import django_filters
from inventory.models import Product, ProductVariant


class ProductFilter(django_filters.FilterSet):
    id = django_filters.UUIDFilter()
    hsn_code = django_filters.CharFilter(lookup_expr='iexact')
    gst_slab = django_filters.NumberFilter()

    class Meta:
        model = Product
        fields = ['id', 'hsn_code', 'gst_slab']



class ProductVariantFilter(django_filters.FilterSet):
    sku = django_filters.CharFilter(lookup_expr='icontains')
    color = django_filters.CharFilter(lookup_expr='iexact')
    size = django_filters.CharFilter(lookup_expr='iexact')

    class Meta:
        model = ProductVariant
        fields = ['sku', 'color', 'size']
