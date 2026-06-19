import requests
from django.core.cache import cache
from .models import AmazonCredentials

def get_lwa_access_token(credentials: AmazonCredentials) -> str:
    """
    Retrieves the ephemeral LWA access token for the given credentials.
    Caches the token in the Django cache with a buffer of 60 seconds before expiration.
    """
    cache_key = f"amazon_lwa_token_{credentials.seller_id}"
    token = cache.get(cache_key)
    if token:
        return token

    # Call Amazon LWA endpoint
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": credentials.lwa_refresh_token,
        "client_id": credentials.lwa_client_id,
        "client_secret": credentials.lwa_client_secret,
    }
    
    response = requests.post("https://api.amazon.com/auth/o2/token", json=payload, timeout=10)
    response.raise_for_status()
    
    data = response.json()
    access_token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))

    # Subtract 60 seconds buffer so we refresh it before it expires on Amazon's side
    timeout = max(expires_in - 60, 60)
    cache.set(cache_key, access_token, timeout=timeout)
    
    return access_token
