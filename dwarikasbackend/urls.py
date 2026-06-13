"""
URL configuration for dwarikasbackend — Dwarikas Central API Engine.

All application routes are namespaced under /api/v1/.
Django admin is excluded (stateless JWT-only API — see settings.py).

References:
  - context/architecture.md — api/views/ system boundary
  - context/feature-spec/spec02-Database_and_appconfiguration.md
"""

from django.urls import path, include

urlpatterns = [
    path('api/v1/', include('api.urls')),
    path('api/v1/', include('inventory.urls')),
    path('api/v1/', include('tasks.urls')),
    path('api/v1/', include('ondc.urls')),
    path('api/v1/', include('whatsapp.urls')),
    path('api/v1/', include('payments.urls')),    # Spec #17 — Razorpay Payment Gateway
    path('api/v1/', include('pos.urls')),         # Spec #18 — POS Cash Sales & In-Store Bill Generation
    path('api/v1/', include('promotions.urls')),  # Spec #19 — Promotions & Discounts
    path('api/v1/', include('coupons.urls')),     # Spec #20 — Coupon Code Creation & Application
    path('api/v1/', include('gaming.urls')),      # Spec #21 — Gaming Engine Integration & Coupon Rewards
]

