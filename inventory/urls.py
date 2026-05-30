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
