from django.urls import path
from quickcommerce.views import (
    ListingsSyncView,
    ListingStatusView,
    BlinkitPoWebhookView,
    BlinkitAsnSubmitView,
    JioMartOrderWebhookView,
    JioMartManifestCloseView,
    OperationalMetricsView,
    JioMartBatchPollView
)

urlpatterns = [
    path('quickcommerce/listings/sync/', ListingsSyncView.as_view(), name='quickcommerce-listings-sync'),
    path('quickcommerce/listings/<uuid:product_id>/status/', ListingStatusView.as_view(), name='quickcommerce-listings-status'),
    path('quickcommerce/blinkit/webhook/po/', BlinkitPoWebhookView.as_view(), name='quickcommerce-blinkit-po-webhook'),
    path('quickcommerce/blinkit/asn/submit/', BlinkitAsnSubmitView.as_view(), name='quickcommerce-blinkit-asn-submit'),
    path('quickcommerce/jiomart/webhook/order/', JioMartOrderWebhookView.as_view(), name='quickcommerce-jiomart-order-webhook'),
    path('quickcommerce/jiomart/manifest/close/', JioMartManifestCloseView.as_view(), name='quickcommerce-jiomart-manifest-close'),
    path('quickcommerce/metrics/', OperationalMetricsView.as_view(), name='quickcommerce-metrics'),
    path('quickcommerce/tasks/poll-jiomart-status/', JioMartBatchPollView.as_view(), name='quickcommerce-task-poll-jiomart'),
]
