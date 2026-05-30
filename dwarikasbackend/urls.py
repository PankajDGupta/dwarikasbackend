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
    # API v1 routes registered by the api app (wired in Spec #03+)
    # path('api/v1/', include('api.urls')),
]
