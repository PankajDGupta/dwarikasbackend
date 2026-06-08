from django.urls import path
from pos.views import (
    PosCartCreateView,
    PosCartItemAddView,
    PosCartItemRemoveView,
    PosCartDetailView,
    PosCartConfirmView,
    PosCartAbandonView,
    PosBillView,
)

urlpatterns = [
    path('pos/cart/', PosCartCreateView.as_view(), name='pos-cart-create'),
    path('pos/cart/<uuid:cart_id>/', PosCartDetailView.as_view(), name='pos-cart-detail'),
    path('pos/cart/<uuid:cart_id>/items/', PosCartItemAddView.as_view(), name='pos-cart-item-add'),
    path('pos/cart/<uuid:cart_id>/items/<uuid:item_id>/', PosCartItemRemoveView.as_view(), name='pos-cart-item-remove'),
    path('pos/cart/<uuid:cart_id>/confirm/', PosCartConfirmView.as_view(), name='pos-cart-confirm'),
    path('pos/cart/<uuid:cart_id>/abandon/', PosCartAbandonView.as_view(), name='pos-cart-abandon'),
    path('pos/bill/<uuid:order_id>/', PosBillView.as_view(), name='pos-bill'),
]
