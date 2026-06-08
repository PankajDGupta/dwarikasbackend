from django.urls import path
from promotions.views import (
    PromotionListCreateView,
    PromotionDetailView,
    ActivePromotionsView,
    PromotionItemCreateView,
    PromotionItemDeleteView,
    SharePromotionWhatsAppView,
)

urlpatterns = [
    path('promotions/', PromotionListCreateView.as_view(), name='promotion-list-create'),
    path('promotions/active/', ActivePromotionsView.as_view(), name='promotion-active-list'),
    path('promotions/<uuid:id>/', PromotionDetailView.as_view(), name='promotion-detail'),
    path('promotions/<uuid:promotion_id>/items/', PromotionItemCreateView.as_view(), name='promotion-item-create'),
    path('promotions/<uuid:promotion_id>/items/<uuid:item_id>/', PromotionItemDeleteView.as_view(), name='promotion-item-delete'),
    path('promotions/<uuid:id>/share/whatsapp/', SharePromotionWhatsAppView.as_view(), name='promotion-share-whatsapp'),
]
