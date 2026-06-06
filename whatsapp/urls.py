from django.urls import path
from whatsapp.views import WhatsAppWebhookView

urlpatterns = [
    path('whatsapp/webhook/', WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),
]
