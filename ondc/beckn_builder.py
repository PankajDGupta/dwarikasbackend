"""
Helpers to build Beckn-compliant context and catalog response payloads.
"""
import os
import uuid
from datetime import datetime, timezone

SUBSCRIBER_ID = os.environ.get('ONDC_SUBSCRIBER_ID', 'dwarikas.com')
SELLER_CITY = os.environ.get('ONDC_SELLER_CITY', 'std:080')
DOMAIN = os.environ.get('ONDC_DOMAIN', 'nic2004:52110')   # Retail domain code for fashion/apparel


def make_ack_response(context: dict, error: dict = None) -> dict:
    """Returns a synchronous ACK or NACK for the incoming Beckn request."""
    ack = {
        'context': {**context, 'action': f"on_{context.get('action', '')}"},
        'message': {
            'ack': {'status': 'NACK' if error else 'ACK'}
        },
    }
    if error:
        ack['error'] = error
    return ack


def make_on_search_payload(context: dict, products: list) -> dict:
    """Builds the /on_search catalog payload from a list of ProductVariant dicts."""
    return {
        'context': {**context, 'action': 'on_search'},
        'message': {
            'catalog': {
                'bpp/descriptor': {'name': 'Dwarikas'},
                'bpp/providers': [
                    {
                        'id': SUBSCRIBER_ID,
                        'descriptor': {'name': 'Dwarikas Store'},
                        'items': [
                            {
                                'id': str(p['id']),
                                'descriptor': {'name': p['name'], 'code': p['sku']},
                                'price': {
                                    'currency': 'INR',
                                    'value': str(p['retail_price']),
                                },
                                'quantity': {'available': {'count': p['atp']}},
                                'category_id': DOMAIN,
                            }
                            for p in products
                        ],
                    }
                ],
            }
        },
    }


def make_on_select_payload(context: dict, items: list, quote_price: str, breakup: list, error: dict = None) -> dict:
    """Builds the /on_select payload."""
    payload = {
        'context': {**context, 'action': 'on_select'},
        'message': {
            'order': {
                'provider': {'id': SUBSCRIBER_ID},
                'items': items,
                'quote': {
                    'price': {'currency': 'INR', 'value': str(quote_price)},
                    'breakup': breakup,
                    'ttl': 'PT10M'
                }
            }
        }
    }
    if error:
        payload['error'] = error
    return payload


def make_on_init_payload(context: dict, order_details: dict, error: dict = None) -> dict:
    """Builds the /on_init payload."""
    payload = {
        'context': {**context, 'action': 'on_init'},
        'message': {
            'order': order_details
        }
    }
    if error:
        payload['error'] = error
    return payload


def make_on_confirm_payload(context: dict, order_details: dict, error: dict = None) -> dict:
    """Builds the /on_confirm payload."""
    payload = {
        'context': {**context, 'action': 'on_confirm'},
        'message': {
            'order': order_details
        }
    }
    if error:
        payload['error'] = error
    return payload


def make_on_status_payload(context: dict, order_details: dict, error: dict = None) -> dict:
    """Builds the /on_status payload."""
    payload = {
        'context': {**context, 'action': 'on_status'},
        'message': {
            'order': order_details
        }
    }
    if error:
        payload['error'] = error
    return payload


def make_on_cancel_payload(context: dict, order_details: dict, error: dict = None) -> dict:
    """Builds the /on_cancel payload."""
    payload = {
        'context': {**context, 'action': 'on_cancel'},
        'message': {
            'order': order_details
        }
    }
    if error:
        payload['error'] = error
    return payload
