from django.urls import path
from coupons.views import (
    CouponListCreateView,
    CouponDetailView,
    CouponValidateView,
    ApplyCouponView,
    RemoveCouponView,
)

urlpatterns = [
    path('coupons/', CouponListCreateView.as_view(), name='coupon-list-create'),
    path('coupons/<uuid:id>/', CouponDetailView.as_view(), name='coupon-detail'),
    path('coupons/validate/<str:code>/', CouponValidateView.as_view(), name='coupon-validate'),
    path('checkout/apply-coupon/', ApplyCouponView.as_view(), name='coupon-apply'),
    path('checkout/remove-coupon/<uuid:reservation_id>/', RemoveCouponView.as_view(), name='coupon-remove'),
]
