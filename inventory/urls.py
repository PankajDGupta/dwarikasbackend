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
]
