from django.urls import path
from promotions.views import (
    PromotionListCreateView,
    PromotionDetailView,
    ActivePromotionsView,
    PromotionItemCreateView,
    PromotionItemDeleteView,
    SharePromotionWhatsAppView,
)
from promotions.suggestion_views import (
    DiscountSuggestionListView,
    DiscountSuggestionDetailView,
    ApproveSuggestionView,
    DismissSuggestionView,
)

urlpatterns = [
    path('promotions/', PromotionListCreateView.as_view(), name='promotion-list-create'),
    path('promotions/active/', ActivePromotionsView.as_view(), name='promotion-active-list'),
    path('promotions/<uuid:id>/', PromotionDetailView.as_view(), name='promotion-detail'),
    path('promotions/<uuid:promotion_id>/items/', PromotionItemCreateView.as_view(), name='promotion-item-create'),
    path('promotions/<uuid:promotion_id>/items/<uuid:item_id>/', PromotionItemDeleteView.as_view(), name='promotion-item-delete'),
    path('promotions/<uuid:id>/share/whatsapp/', SharePromotionWhatsAppView.as_view(), name='promotion-share-whatsapp'),
    path('promotions/suggestions/', DiscountSuggestionListView.as_view(), name='discount-suggestion-list'),
    path('promotions/suggestions/<uuid:id>/', DiscountSuggestionDetailView.as_view(), name='discount-suggestion-detail'),
    path('promotions/suggestions/<uuid:id>/approve/', ApproveSuggestionView.as_view(), name='discount-suggestion-approve'),
    path('promotions/suggestions/<uuid:id>/dismiss/', DismissSuggestionView.as_view(), name='discount-suggestion-dismiss'),
]

