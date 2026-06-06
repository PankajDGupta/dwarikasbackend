import jwt
import time
from unittest.mock import patch
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient
from axes.utils import reset


def generate_test_jwt(role, user_agent=None, jti="test-jti-123", email="user@example.com", expires_in=3600):
    """Helper: generate a valid Supabase JWT payload for tests."""
    payload = {
        "aud": "authenticated",
        "sub": "user-uuid-12345",
        "email": email,
        "jti": jti,
        "exp": int(time.time()) + expires_in,
        "app_metadata": {
            "role": role
        }
    }
    if user_agent:
        payload["app_metadata"]["user_agent"] = user_agent
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@override_settings(
    SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long",
)
class SecurityHardeningTests(TestCase):

    def setUp(self):
        reset()  # Reset Axes records before each test
        cache.clear()  # Clear cache before each test
        self.client = APIClient()
        self.auth_token = generate_test_jwt("customer", jti="valid-session-jti")
        self.header_with_ua = generate_test_jwt("customer", user_agent="Mozilla/5.0")

    def tearDown(self):
        reset()  # Clean up Axes lockout records
        cache.clear()

    # ── 1. Health Check Tests ─────────────────────────────────────────────────
    def test_health_check_healthy(self):
        """Verify the health check endpoint returns 200 and details when services are up."""
        url = reverse('health-check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'healthy')
        self.assertEqual(response.data['checks']['database'], 'ok')
        self.assertEqual(response.data['checks']['redis'], 'ok')

    @patch('django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection')
    def test_health_check_db_failure(self, mock_ensure):
        """Verify 503 degraded response when the database connection fails."""
        mock_ensure.side_effect = Exception("Connection refused")
        url = reverse('health-check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['status'], 'degraded')
        self.assertIn("error", response.data['checks']['database'])

    @patch('django.core.cache.cache.set')
    def test_health_check_redis_failure(self, mock_cache_set):
        """Verify 503 degraded response when the cache fails."""
        mock_cache_set.side_effect = Exception("Redis connection failed")
        url = reverse('health-check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data['status'], 'degraded')
        self.assertIn("error", response.data['checks']['redis'])

    # ── 2. User-Agent Binding Tests ──────────────────────────────────────────
    def test_user_agent_matches_token_passes(self):
        """Verify access is granted when the request User-Agent matches the token claim."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.header_with_ua}", HTTP_USER_AGENT="Mozilla/5.0")
        url = reverse('health-check')  # Any endpoint
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_user_agent_mismatch_returns_403(self):
        """Verify access is rejected with a 403 when the User-Agent header differs from the token claim."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.header_with_ua}", HTTP_USER_AGENT="Chrome/10.0")
        url = reverse('health-check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.json(),
            {'error': 'Session token is bound to a different device. Please log in again.'}
        )

    def test_no_user_agent_claim_in_token_passes(self):
        """Verify access is granted if the token does not specify a user_agent binding."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.auth_token}", HTTP_USER_AGENT="Chrome/10.0")
        url = reverse('health-check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ── 3. JTI Revocation / Logout Tests ──────────────────────────────────────
    def test_logout_revokes_jti(self):
        """Verify logout adds the JTI to the revocation list, and subsequent requests fail."""
        url_logout = reverse('auth-logout')
        url_health = reverse('health-check')

        # First verify the token works fine
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.auth_token}")
        response = self.client.post(url_logout)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Logged out successfully.')

        # Subsequent request with same token must fail with 401
        response = self.client.get(url_health)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(
            response.json(),
            {'error': 'This session has been revoked. Please log in again.'}
        )

    # ── 4. Brute Force Lockout Tests ──────────────────────────────────────────
    def test_brute_force_axes_lockout(self):
        """Verify that 5 failed login attempts lock out the client IP on the 6th attempt."""
        url_logout = reverse('auth-logout')

        # Make 5 failed attempts using an invalid token signature
        invalid_token = "Bearer this.is.invalidgarbage"
        for _ in range(5):
            self.client.credentials(HTTP_AUTHORIZATION=invalid_token)
            response = self.client.post(url_logout)
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 6th attempt (even with valid token or anon) should be blocked by Axes
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.auth_token}")
        response = self.client.post(url_logout)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.json(),
            {'error': 'Account locked due to too many failed login attempts.'}
        )

    # ── 5. Throttling Rate Limiting Tests ─────────────────────────────────────
    def test_rate_limiting_anon_and_user(self):
        """Verify anonymous and authenticated rate limits block excessive requests with 429."""
        from rest_framework.throttling import SimpleRateThrottle

        # Save current throttle rates
        original_rates = SimpleRateThrottle.THROTTLE_RATES.copy()
        
        try:
            # Force DRF to use lower limits during the test
            SimpleRateThrottle.THROTTLE_RATES['anon'] = '2/minute'
            SimpleRateThrottle.THROTTLE_RATES['user'] = '3/minute'

            url_public = reverse('product-list')

            # 1. Test Anon limits (2 requests per minute)
            response1 = self.client.get(url_public)
            self.assertEqual(response1.status_code, status.HTTP_200_OK)

            response2 = self.client.get(url_public)
            self.assertEqual(response2.status_code, status.HTTP_200_OK)

            response3 = self.client.get(url_public)
            self.assertEqual(response3.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

            # Reset cache so throttle state is cleared for the user test
            cache.clear()

            # 2. Test User limits (3 requests per minute)
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.auth_token}")

            for _ in range(3):
                response = self.client.get(url_public)
                self.assertEqual(response.status_code, status.HTTP_200_OK)

            # 4th request should get 429
            response_throttled = self.client.get(url_public)
            self.assertEqual(response_throttled.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        finally:
            # Restore original throttle rates
            SimpleRateThrottle.THROTTLE_RATES = original_rates
