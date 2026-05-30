# Step 3: Custom Stateless Authentication
(api/authentication.py)
Since Supabase issues standard HS256 JWT tokens signed with your project's JWT secret, the DRF application can validate them entirely locally. This completely removes external network round-trips to verify identity states, enabling low-latency responses.
import jwt
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework import authentication, exceptions

class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header:
            return None

        try:
            # Parse target layout structure: "Bearer <JWT_TOKEN>"
            auth_type, token = auth_header.split(' ')
            if auth_type.lower() != 'bearer':
                return None
        except ValueError:
            raise exceptions.AuthenticationFailed('Invalid authorization header structure. Format as: Bearer <token>')

        if not settings.SUPABASE_JWT_SECRET:
            raise exceptions.AuthenticationFailed('Server misconfiguration: Cryptographic signing secret is missing.')

        try:
            # Locally decode the token signature against the Secret Manager key instance
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated"
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed('The provided authentication token has expired.')
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed('Cryptographic verification failed against token signature.')

        # Extract system identity parameters from Supabase context mapping keys
        supabase_uid = payload.get('sub')
        email = payload.get('email')

        if not supabase_uid:
            raise exceptions.AuthenticationFailed('Token payload lacks a valid unique user subject identifier.')

        # Bind token payload details to an ephemeral, unpersisted User mock object to maintain true statelessness
        user = User(username=supabase_uid, email=email)
        user.is_authenticated = True
        
        # Ingest App Metadata Claims (e.g., system roles) directly onto the user reference object
        app_metadata = payload.get('app_metadata', {})
        user.role = app_metadata.get('role', 'customer')

        return (user, token)