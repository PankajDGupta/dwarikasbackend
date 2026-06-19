from django.urls import path
from .views import AmazonListingSyncView, AmazonListingStatusView, SQSWebhookView

urlpatterns = [
    path('amazon/listings/sync/', AmazonListingSyncView.as_view(), name='amazon-listings-sync'),
    path('amazon/listings/<uuid:product_id>/status/', AmazonListingStatusView.as_view(), name='amazon-listings-status'),
    path('amazon/webhooks/sqs-receiver/', SQSWebhookView.as_view(), name='amazon-sqs-webhook'),
]
