# Spec 06 — Product Catalog API (Read & Admin Write)

## Goal
Implement REST endpoints for the product catalog. Public clients (web/mobile) can list and retrieve products and their variants without authentication. Staff and managers can create, update, and delete catalog entries.

The catalog is designed as a **multi-category platform** — it supports Grocery & FMCG items today, and is extended (as of 2026-06-01) to also support **Apparel & Clothing** and **General Merchandise**. A `product_type` discriminator on each product record drives which optional fields apply.

---

## Scope
- `GET /api/v1/products/` — paginated product list (public)
- `GET /api/v1/products/<uuid:id>/` — single product with variants (public)
- `POST /api/v1/products/` — create product (staff/manager only)
- `PATCH /api/v1/products/<uuid:id>/` — partial update (manager only)
- `DELETE /api/v1/products/<uuid:id>/` — delete product (manager only)
- `GET /api/v1/products/<uuid:id>/variants/` — list variants for a product (public)
- `POST /api/v1/products/<uuid:id>/variants/` — add variant (staff/manager only)
- Filtering: by `id`, `hsn_code`, `gst_slab`, `product_type`, `brand`, `category`, `gender_target`, `color`, `size`, `sku`
- Search: across `name`, `hsn_code`, `brand`, `description`, `material`
- Install and configure `django-filter`

---

## Product Type Model

The `product_type` field drives which optional fields are meaningful for each product:

| `product_type` | Relevant Optional Fields |
|---|---|
| `grocery` | `dietary_type`, `weight_volume`, `net_quantity`, `unit_of_measure` (on variant) |
| `apparel` | `material`, `gender_target`, `fit_type`, `size` / `color` (on variant) |
| `general` | `description`, `brand`, `category` |

All fields are always present in the API response — clients read `product_type` to decide which to render.

---

## Files to Create / Modify

### `inventory/models.py` — Modified

```python
class Product(models.Model):
    PRODUCT_TYPE_CHOICES = [
        ('grocery', 'Grocery & FMCG'),
        ('apparel', 'Apparel & Clothing'),
        ('general', 'General Merchandise'),
    ]
    DIETARY_CHOICES = [
        ('veg', 'Vegetarian'), ('non-veg', 'Non-Vegetarian'),
        ('egg', 'Contains Egg'), ('none', 'Not Applicable'),
    ]
    GENDER_TARGET_CHOICES = [
        ('men', 'Men'), ('women', 'Women'), ('unisex', 'Unisex'),
        ('boys', 'Boys'), ('girls', 'Girls'), ('none', 'Not Applicable'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    hsn_code = models.TextField()
    gst_slab = models.DecimalField(max_digits=5, decimal_places=2, default=18.00)

    # Classification (required)
    product_type = models.TextField(choices=PRODUCT_TYPE_CHOICES, default='general')

    # Common fields (all types)
    brand = models.TextField(null=True, blank=True)
    category = models.TextField(null=True, blank=True)
    subcategory = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    image_url = models.TextField(null=True, blank=True)

    # Grocery-specific
    dietary_type = models.TextField(choices=DIETARY_CHOICES, default='none')

    # Apparel-specific
    material = models.TextField(null=True, blank=True)
    gender_target = models.TextField(choices=GENDER_TARGET_CHOICES, default='none')
    fit_type = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'products'
```

---

### `inventory/serializers.py` — New file

```python
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
    """Full read serializer — returned on GET requests."""
    variants = ProductVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'hsn_code', 'gst_slab',
            'product_type',
            'brand', 'category', 'subcategory', 'description', 'image_url',
            'dietary_type',
            'material', 'gender_target', 'fit_type',
            'created_at', 'variants',
        ]
        read_only_fields = ['id', 'created_at']


class ProductWriteSerializer(serializers.ModelSerializer):
    """Slim write serializer — excludes nested variants to prevent accidental bulk writes."""
    class Meta:
        model = Product
        fields = [
            'name', 'hsn_code', 'gst_slab',
            'product_type',
            'brand', 'category', 'subcategory', 'description', 'image_url',
            'dietary_type',
            'material', 'gender_target', 'fit_type',
        ]
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
    product_type = django_filters.CharFilter(lookup_expr='iexact')
    brand = django_filters.CharFilter(lookup_expr='icontains')
    category = django_filters.CharFilter(lookup_expr='icontains')
    gender_target = django_filters.CharFilter(lookup_expr='iexact')

    class Meta:
        model = Product
        fields = ['id', 'hsn_code', 'gst_slab', 'product_type', 'brand', 'category', 'gender_target']


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
    search_fields = ['name', 'hsn_code', 'brand', 'description', 'material']
    ordering_fields = ['created_at', 'name', 'gst_slab', 'product_type']

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

### `supabase/snippets/002_product_catalog_multicategory.sql` — Supabase DDL migration

Run this against the Supabase database to apply schema changes:

```sql
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS product_type TEXT NOT NULL DEFAULT 'general'
        CHECK (product_type IN ('grocery', 'apparel', 'general'));

-- Backfill: existing rows are grocery items
UPDATE public.products SET product_type = 'grocery' WHERE product_type = 'general';

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS material TEXT,
    ADD COLUMN IF NOT EXISTS gender_target TEXT DEFAULT 'none'
        CHECK (gender_target IN ('men', 'women', 'unisex', 'boys', 'girls', 'none')),
    ADD COLUMN IF NOT EXISTS fit_type TEXT;

CREATE INDEX IF NOT EXISTS idx_products_product_type ON public.products (product_type);
CREATE INDEX IF NOT EXISTS idx_products_gender_target ON public.products (gender_target);
```

---

### `inventory/migrations/0002_product_multicategory_fields.py` — Django state migration

This migration updates Django's migration state only (models are `managed = False` — no DDL runs against the database):

```python
# See the file for full content.
# Adds: product_type, material, gender_target, fit_type fields to Product model state.
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

### Filter Query Parameters

| Parameter | Applies To | Example |
|---|---|---|
| `?product_type=apparel` | Product list | Browse only clothing |
| `?brand=levi` | Product list | Filter by brand (partial, case-insensitive) |
| `?category=shirts` | Product list | Filter by category |
| `?gender_target=women` | Product list | Browse women's apparel |
| `?hsn_code=6101` | Product list | Filter by HSN code |
| `?gst_slab=5` | Product list | Filter by GST rate |
| `?search=cotton` | Product list | Full-text across name, brand, description, material |
| `?color=blue` | Variant list | Filter by color |
| `?size=M` | Variant list | Filter by size (works for both apparel and grocery units) |

---

## Acceptance Criteria

- [ ] `GET /api/v1/products/` returns paginated JSON list with 200 status
- [ ] `POST /api/v1/products/` without a JWT returns 401
- [ ] `POST /api/v1/products/` with a customer JWT returns 403
- [ ] `POST /api/v1/products/` with a staff JWT creates a product and returns 201
- [ ] Filter `?hsn_code=6101` narrows results correctly
- [ ] Filter `?product_type=apparel` returns only apparel items
- [ ] Filter `?gender_target=women` returns only women's products
- [ ] Search `?search=cotton` matches products with 'cotton' in name, description, or material
- [ ] Creating an apparel product with `material`, `gender_target`, `fit_type` returns them in GET response
- [ ] Creating a grocery product with `dietary_type=veg` returns it in GET response
- [ ] `python manage.py check` passes with zero errors
- [ ] No N+1 queries on the list endpoint (verify with `django-debug-toolbar` or `assertNumQueries`)
