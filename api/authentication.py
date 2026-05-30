"""
Supabase JWT Authentication — Spec #03.

Validates Supabase-issued HS256 JWTs locally against the SUPABASE_JWT_SECRET
environment variable (sourced from Google Cloud Secret Manager at runtime).
No external network round-trips are made during token verification, keeping
response latency within the <100ms target defined in the project success criteria.

Auth model:
  - Token format  : Authorization: Bearer <JWT>
  - Algorithm     : HS256 (symmetric — Supabase project JWT secret)
  - Audience claim: "authenticated" (Supabase default for logged-in users)
  - User object   : ephemeral, in-memory Django User instance (never persisted)
  - Role claim    : read from app_metadata.role; defaults to "customer"
"""

import jwt
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework import authentication, exceptions


class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    """
    Stateless DRF authenticator for Supabase HS256 JWTs.

    On a valid token the method returns a (user, token) 2-tuple where `user`
    is an ephemeral, unpersisted Django User object carrying the Supabase
    subject UID, email address, and resolved role.  The object is never saved
    to the database — this keeps the service entirely stateless.

    Returns None (not an error) when no Authorization header is present so
    that DRF's permission layer can apply its own anonymous-access rules.
    """

    def authenticate(self, request):
        # ── 1. Header extraction ──────────────────────────────────────────────
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header:
            # No header → let DRF decide whether the endpoint allows anonymous
            # access (via permission classes).
            return None

        # ── 2. Header structure validation ────────────────────────────────────
        try:
            # Expected layout: "Bearer <JWT_TOKEN>"
            auth_type, token = auth_header.split(" ", 1)
            if auth_type.lower() != "bearer":
                return None
        except ValueError:
            raise exceptions.AuthenticationFailed(
                "Invalid authorization header structure. "
                "Expected format: Bearer <token>"
            )

        # ── 3. Server-side secret guard ───────────────────────────────────────
        if not getattr(settings, "SUPABASE_JWT_SECRET", None):
            raise exceptions.AuthenticationFailed(
                "Server misconfiguration: Cryptographic signing secret is missing."
            )

        # ── 4. Local JWT decode & signature verification ──────────────────────
        try:
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                # Supabase sets aud="authenticated" for every logged-in user.
                audience="authenticated",
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed(
                "The provided authentication token has expired."
            )
        except jwt.InvalidAudienceError:
            raise exceptions.AuthenticationFailed(
                "Token audience claim does not match the expected value."
            )
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed(
                "Cryptographic verification failed against token signature."
            )

        # ── 5. Claim extraction ───────────────────────────────────────────────
        supabase_uid = payload.get("sub")
        email = payload.get("email", "")

        if not supabase_uid:
            raise exceptions.AuthenticationFailed(
                "Token payload lacks a valid unique user subject identifier."
            )

        # ── 6. Ephemeral user construction (stateless — never persisted) ───────
        # Binding payload details to an in-memory Django User object allows
        # standard DRF permission classes (IsAuthenticated, IsAdminUser, etc.)
        # to work without a database lookup on every request.
        user = User(username=supabase_uid, email=email)
        user.is_authenticated = True  # type: ignore[assignment]

        # Ingest app_metadata role claims onto the user reference.
        # Supabase stores custom roles in app_metadata; fall back to "customer".
        app_metadata = payload.get("app_metadata", {})
        user.role = app_metadata.get("role", "customer")  # type: ignore[attr-defined]

        # Attach the full decoded payload for downstream views that need
        # extra Supabase claims (e.g., user_metadata, session_id).
        user.supabase_payload = payload  # type: ignore[attr-defined]

        return (user, token)
