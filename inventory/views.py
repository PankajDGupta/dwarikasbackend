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
