from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Django app config for the api module — the primary REST gateway."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'
