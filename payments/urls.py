"""
URL configuration for payments/ Django app — Spec #17.

All routes mount under /api/v1/payments/ via dwarikasbackend/urls.py.
"""

from django.urls import path

from payments.views import (
    CreatePaymentOrderView,
    PaymentStatusView,
    RazorpayWebhookView,
    RefundView,
    VerifyPaymentView,
)

urlpatterns = [
    path('payments/create-order/', CreatePaymentOrderView.as_view(), name='payment-create-order'),
    path('payments/verify/', VerifyPaymentView.as_view(), name='payment-verify'),
    path('payments/webhook/', RazorpayWebhookView.as_view(), name='payment-webhook'),
    path('payments/refund/', RefundView.as_view(), name='payment-refund'),
    path('payments/status/<uuid:order_id>/', PaymentStatusView.as_view(), name='payment-status'),
]
