"""
Logout endpoint: adds the caller's JWT jti to the Redis revocation blocklist.
"""
import jwt as pyjwt
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from api.middleware import revoke_jti


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return Response({'error': 'No token provided.'}, status=status.HTTP_400_BAD_REQUEST)

        raw_token = auth_header.split(' ', 1)[1]
        try:
            payload = pyjwt.decode(
                raw_token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=['HS256'],
                audience='authenticated',
            )
            jti = payload.get('jti')
            exp = payload.get('exp', 0)
        except pyjwt.PyJWTError:
            return Response({'error': 'Invalid token.'}, status=status.HTTP_400_BAD_REQUEST)

        if jti:
            import time
            ttl = max(0, int(exp - time.time()))
            revoke_jti(jti, ttl_seconds=ttl or 3600)

        return Response({'message': 'Logged out successfully.'}, status=status.HTTP_200_OK)
