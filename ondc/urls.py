"""
URL routes for the ONDC Seller Node integration.
"""
from django.urls import path
from ondc.views import (
    OndcSearchView,
    OndcSelectView,
    OndcInitView,
    OndcConfirmView,
    OndcStatusView,
    OndcCancelView,
)
from ondc.tasks import OndcTaskCallbackView

urlpatterns = [
    path('ondc/search/', OndcSearchView.as_view(), name='ondc-search'),
    path('ondc/select/', OndcSelectView.as_view(), name='ondc-select'),
    path('ondc/init/', OndcInitView.as_view(), name='ondc-init'),
    path('ondc/confirm/', OndcConfirmView.as_view(), name='ondc-confirm'),
    path('ondc/status/', OndcStatusView.as_view(), name='ondc-status'),
    path('ondc/cancel/', OndcCancelView.as_view(), name='ondc-cancel'),
    
    # Internal Cloud Task Callback Worker
    path('ondc/tasks/callback/', OndcTaskCallbackView.as_view(), name='ondc-tasks-callback'),
]
