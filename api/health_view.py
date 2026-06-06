"""
Health-check endpoint for Cloud Run liveness probes.
"""
from django.db import connection
from django.core.cache import cache
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        checks = {}

        # Database connectivity
        try:
            connection.ensure_connection()
            checks['database'] = 'ok'
        except Exception as e:
            checks['database'] = f'error: {str(e)}'

        # Cache connectivity
        try:
            cache.set('health_ping', '1', timeout=5)
            checks['redis'] = 'ok' if cache.get('health_ping') == '1' else 'miss'
        except Exception as e:
            checks['redis'] = f'error: {str(e)}'

        all_ok = all(v == 'ok' for v in checks.values())
        return Response(
            {'status': 'healthy' if all_ok else 'degraded', 'checks': checks},
            status=200 if all_ok else 503,
        )
