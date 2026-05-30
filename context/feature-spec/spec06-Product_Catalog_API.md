# Spec 06 — Product Catalog API (Read & Admin Write)

## Goal
Implement REST endpoints for the product catalog. Public clients (web/mobile) can list and retrieve products and their variants without authentication. Staff and managers can create, update, and delete catalog entries.

---

## Scope
- `GET /api/v1/products/` — paginated product list (public)
- `GET /api/v1/products/<uuid:id>/` — single product with variants (public)
- `POST /api/v1/products/` — create product (staff/manager only)
- `PATCH /api/v1/products/<uuid:id>/` — partial update (manager only)
- `GET /api/v1/products/<uuid:id>/variants/` — list variants for a product (public)
- `POST /api/v1/products/<uuid:id>/variants/` — add variant (staff/manager only)
- Filtering: by `id`, `hsn_code`, `gst_slab`, `color`, `size`, `sku`
- Install and configure `django-filter`


---

## Files to Create / Modify

### `inventory/serializers.py` — New file

```python
from rest_framework import serializers
from inventory.models import Product, ProductVariant


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = [
            'id', 'sku', 'barcode', 'size', 'color',
            'stock_quantity', 'retail_price', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'name', 'hsn_code', 'gst_slab', 'created_at', 'variants']
        read_only_fields = ['id', 'created_at']


class ProductWriteSerializer(serializers.ModelSerializer):
    """Slim write serializer — excludes nested variants to prevent accidental bulk writes."""
    class Meta:
        model = Product
        fields = ['name', 'hsn_code', 'gst_slab']
```

---

### `inventory/filters.py` — New file

```python
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
```

---

### `inventory/views.py` — New file

```python
from rest_framework import generics, permissions
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from inventory.models import Product, ProductVariant
from inventory.serializers import (
    ProductSerializer, ProductWriteSerializer, ProductVariantSerializer
)
from inventory.filters import ProductFilter, ProductVariantFilter
from api.permissions import IsStaffOrManager, IsManager


class ProductListCreateView(generics.ListCreateAPIView):
    queryset = Product.objects.prefetch_related('variants').order_by('-created_at')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ProductFilter
    search_fields = ['name', 'hsn_code']
    ordering_fields = ['created_at', 'name', 'gst_slab']

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ProductWriteSerializer
        return ProductSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.AllowAny()]
        return [IsStaffOrManager()]


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.prefetch_related('variants')
    lookup_field = 'id'

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return ProductWriteSerializer
        return ProductSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.AllowAny()]
        if self.request.method == 'DELETE':
            return [IsManager()]
        return [IsStaffOrManager()]


class ProductVariantListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductVariantSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = ProductVariantFilter
    search_fields = ['sku', 'barcode']

    def get_queryset(self):
        return ProductVariant.objects.filter(
            product_id=self.kwargs['product_id']
        ).order_by('-created_at')

    def get_permissions(self):
        if self.request.method == 'GET':
            return [permissions.AllowAny()]
        return [IsStaffOrManager()]

    def perform_create(self, serializer):
        serializer.save(product_id=self.kwargs['product_id'])
```

---

### `inventory/urls.py` — New file

```python
from django.urls import path
from inventory.views import (
    ProductListCreateView,
    ProductDetailView,
    ProductVariantListCreateView,
)

urlpatterns = [
    path('products/', ProductListCreateView.as_view(), name='product-list'),
    path('products/<uuid:id>/', ProductDetailView.as_view(), name='product-detail'),
    path('products/<uuid:product_id>/variants/', ProductVariantListCreateView.as_view(), name='variant-list'),
]
```

---

### `dwarikasbackend/urls.py` — Include inventory routes

```python
from django.urls import path, include

urlpatterns = [
    path('api/v1/', include('api.urls')),
    path('api/v1/', include('inventory.urls')),
]
```

---

### `dwarikasbackend/settings.py` — Enable django-filter

```python
REST_FRAMEWORK = {
    ...
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
}
```

Add to `pyproject.toml` dependencies:
```
django-filter>=24.0
```

---

## API Reference

| Method | Endpoint | Auth Required | Role |
|--------|----------|---------------|------|
| GET | `/api/v1/products/` | No | Public |
| POST | `/api/v1/products/` | Yes | staff, manager |
| GET | `/api/v1/products/<id>/` | No | Public |
| PATCH | `/api/v1/products/<id>/` | Yes | staff, manager |
| DELETE | `/api/v1/products/<id>/` | Yes | manager |
| GET | `/api/v1/products/<id>/variants/` | No | Public |
| POST | `/api/v1/products/<id>/variants/` | Yes | staff, manager |

---

## Acceptance Criteria

- [ ] `GET /api/v1/products/` returns paginated JSON list with 200 status
- [ ] `POST /api/v1/products/` without a JWT returns 401
- [ ] `POST /api/v1/products/` with a customer JWT returns 403
- [ ] `POST /api/v1/products/` with a staff JWT creates a product and returns 201
- [ ] Filter `?hsn_code=6101` narrows results correctly
- [ ] Search `?search=cotton` returns name-matching products
- [ ] `python manage.py check` passes with zero errors
- [ ] No N+1 queries on the list endpoint (verify with `django-debug-toolbar` or `assertNumQueries`)
