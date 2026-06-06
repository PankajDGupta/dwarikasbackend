from django.urls import path
from api.logout_view import LogoutView
from api.health_view import HealthCheckView

urlpatterns = [
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('health/', HealthCheckView.as_view(), name='health-check'),
]
