import time
import requests
from django.conf import settings
from django.core.cache import cache
from .models import AmazonCredentials
from .lwa_client import get_lwa_access_token

def get_sp_api_base_url(region: str) -> str:
    """
    Returns the SP-API base URL based on the region.
    """
    urls = {
        'NA': 'https://sellingpartnerapi-na.amazon.com',
        'EU': 'https://sellingpartnerapi-eu.amazon.com',
        'FE': 'https://sellingpartnerapi-fe.amazon.com',
    }
    return urls.get(region.upper(), 'https://sellingpartnerapi-eu.amazon.com')


def acquire_rate_limit_token(seller_id: str) -> bool:
    """
    Attempts to acquire a token from the token bucket for the seller.
    Returns True if acquired, False otherwise.
    Capacity: 5. Refill rate: 5 per second.
    """
    cache_key = f"amazon_tb_{seller_id}"
    capacity = 5.0
    refill_rate = 5.0  # tokens per second
    
    state = cache.get(cache_key)
    now = time.time()
    
    if state is None:
        tokens = capacity - 1.0
        last_updated = now
    else:
        tokens, last_updated = state
        # Refill tokens based on elapsed time
        elapsed = now - last_updated
        tokens = min(capacity, tokens + elapsed * refill_rate)
        
        if tokens >= 1.0:
            tokens -= 1.0
            last_updated = now
        else:
            return False
            
    cache.set(cache_key, (tokens, last_updated), timeout=60)
    return True


def wait_and_acquire_token(seller_id: str, max_wait_seconds: float = 10.0) -> bool:
    """
    Blocks until a rate limit token is acquired, or max wait time is reached.
    """
    # In unit tests, we can skip rate limiting delays
    if getattr(settings, 'IS_TESTING', False):
        return True

    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        if acquire_rate_limit_token(seller_id):
            return True
        time.sleep(0.2)
    return False


def put_listings_item(credentials: AmazonCredentials, sku: str, payload: dict) -> dict:
    """
    Submits a listings item via SP-API putListingsItem with rate limiting and backoff.
    Handles immediate 429/503 retries with exponential backoff.
    """
    # 1. Acquire rate limit token
    wait_and_acquire_token(credentials.seller_id)

    # 2. Setup endpoint URL
    base_url = getattr(settings, 'AMAZON_SP_API_BASE_URL', None) or get_sp_api_base_url(credentials.region)
    url = f"{base_url.rstrip('/')}/listings/2021-08-01/items/{credentials.seller_id}/{sku}"
    
    params = {
        "marketplaceIds": credentials.primary_marketplace_id
    }

    # 3. Retry loop with exponential backoff
    retries = 0
    max_retries = 4
    delay = 1.0

    while True:
        try:
            access_token = get_lwa_access_token(credentials)
            headers = {
                "x-amzn-AccessToken": access_token,
                "Content-Type": "application/json",
            }
            
            response = requests.put(url, params=params, json=payload, headers=headers, timeout=10)
            
            if response.status_code in [429, 503]:
                if retries < max_retries:
                    time.sleep(delay)
                    retries += 1
                    delay *= 2
                    # Re-acquire rate limit token before retrying
                    wait_and_acquire_token(credentials.seller_id)
                    continue
            
            # Raise exception for 5xx errors (handled by view as 502)
            if response.status_code >= 500 and response.status_code != 503:
                response.raise_for_status()

            # Otherwise, return JSON response (including 200 success or 400/422 validation failure)
            return response.json()
            
        except requests.RequestException as e:
            if retries < max_retries:
                time.sleep(delay)
                retries += 1
                delay *= 2
                continue
            raise e
