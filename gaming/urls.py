from django.urls import path
from gaming.views import (
    GamePlaysEarnedView,
    RecordPlayView,
    AdStatusView,
    GrantAdPlayView,
    UserRewardListView,
    UserRewardDetailView,
    RewardTierListCreateView,
    RewardTierDetailView,
)

urlpatterns = [
    path('gaming/earn/',                    GamePlaysEarnedView.as_view(),      name='gaming-earn'),
    path('gaming/record-play/',             RecordPlayView.as_view(),           name='gaming-record-play'),
    path('gaming/ad-status/',               AdStatusView.as_view(),             name='gaming-ad-status'),
    path('gaming/grant-ad-play/',           GrantAdPlayView.as_view(),          name='gaming-grant-ad-play'),
    path('gaming/rewards/',                 UserRewardListView.as_view(),       name='gaming-reward-list'),
    path('gaming/rewards/<uuid:id>/',       UserRewardDetailView.as_view(),     name='gaming-reward-detail'),
    path('gaming/reward-tiers/',            RewardTierListCreateView.as_view(), name='reward-tier-list'),
    path('gaming/reward-tiers/<uuid:id>/',  RewardTierDetailView.as_view(),     name='reward-tier-detail'),
]
