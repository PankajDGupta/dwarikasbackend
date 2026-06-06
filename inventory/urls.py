from django.urls import path
from inventory.views import (
    ProductListCreateView,
    ProductDetailView,
    ProductVariantListCreateView,
)
from inventory.checkout_views import (
    CheckoutReserveView,
    ReservationListView,
    ReservationReleaseView,
)
from inventory.order_views import (
    OrderConfirmView,
    OrderListView,
    OrderDetailView,
)
from inventory.barcode_views import Code128BarcodeView, EAN13BarcodeView
from inventory.packaging_views import PackagingJobListCreateView, PackagingJobDetailView
from inventory.invoice_views import InvoiceUploadView, InvoiceListView, InvoiceDetailView
from inventory.hitl_views import InvoiceReviewView, LineItemUpdateView, InvoiceConfirmView

urlpatterns = [
    # ── Product Catalog (Spec #06) ─────────────────────────────────────────
    path('products/', ProductListCreateView.as_view(), name='product-list'),
    path('products/<uuid:id>/', ProductDetailView.as_view(), name='product-detail'),
    path('products/<uuid:product_id>/variants/', ProductVariantListCreateView.as_view(), name='variant-list'),

    # ── Checkout Reservation (Spec #07) ────────────────────────────────────
    # POST   /api/v1/checkout/reserve/          — create a stock hold
    # GET    /api/v1/checkout/reserve/list/     — list caller's active reservations
    # DELETE /api/v1/checkout/reserve/<id>/     — release a reservation early
    path('checkout/reserve/', CheckoutReserveView.as_view(), name='checkout-reserve'),
    path('checkout/reserve/list/', ReservationListView.as_view(), name='reservation-list'),
    path('checkout/reserve/<uuid:reservation_id>/', ReservationReleaseView.as_view(), name='reservation-release'),

    # ── Order Confirmation & Management (Spec #08) ──────────────────────────
    # POST   /api/v1/orders/confirm/            — confirm a reservation into a paid order
    # GET    /api/v1/orders/                    — list Caller's historical orders
    # GET    /api/v1/orders/<id>/               — retrieve a single order
    path('orders/confirm/', OrderConfirmView.as_view(), name='order-confirm'),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<uuid:id>/', OrderDetailView.as_view(), name='order-detail'),

    # ── Barcode Generation (Spec #09) ──────────────────────────────────────
    path('barcodes/<str:sku>/code128/', Code128BarcodeView.as_view(), name='barcode-code128'),
    path('barcodes/<str:sku>/ean13/', EAN13BarcodeView.as_view(), name='barcode-ean13'),

    # ── Loose Product Repackaging (Spec #09b) ──────────────────────────────
    path('packaging-jobs/', PackagingJobListCreateView.as_view(), name='packaging-job-list-create'),
    path('packaging-jobs/<uuid:id>/', PackagingJobDetailView.as_view(), name='packaging-job-detail'),

    # ── Invoice Ingestion (Spec #10) ────────────────────────────────────────
    path('invoices/upload/', InvoiceUploadView.as_view(), name='invoice-upload'),
    path('invoices/', InvoiceListView.as_view(), name='invoice-list'),
    path('invoices/<uuid:id>/', InvoiceDetailView.as_view(), name='invoice-detail'),

    # ── HITL Invoice Validation & Confirmation (Spec #12) ──────────────────
    path('invoices/<uuid:id>/review/', InvoiceReviewView.as_view(), name='invoice-review'),
    path('invoices/line-items/<uuid:id>/', LineItemUpdateView.as_view(), name='line-item-update'),
    path('invoices/<uuid:id>/confirm/', InvoiceConfirmView.as_view(), name='invoice-confirm'),
]

