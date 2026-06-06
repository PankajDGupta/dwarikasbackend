"""
WhatsApp Business API client — wraps Meta's Cloud API endpoints.
"""
import os
import requests

WA_PHONE_NUMBER_ID = os.environ.get('WA_PHONE_NUMBER_ID')
WA_API_TOKEN = os.environ.get('WA_API_TOKEN')
WA_API_VERSION = 'v19.0'
WA_API_BASE = f'https://graph.facebook.com/{WA_API_VERSION}'


def _headers() -> dict:
    return {
        'Authorization': f'Bearer {WA_API_TOKEN}',
        'Content-Type': 'application/json',
    }


def send_text_message(to: str, body: str):
    """Send a plain text reply to a WhatsApp number."""
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': body},
    }
    requests.post(f'{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages', json=payload, headers=_headers(), timeout=10)


def send_list_message(to: str, header_text: str, body_text: str, sections: list):
    """
    Send an interactive list message (product catalog or menu).
    `sections` format:
    [
        {
            'title': 'Section Title',
            'rows': [{'id': 'row_id', 'title': 'Item Name', 'description': 'Rs. 499'}]
        }
    ]
    """
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'interactive',
        'interactive': {
            'type': 'list',
            'header': {'type': 'text', 'text': header_text},
            'body': {'text': body_text},
            'action': {
                'button': 'Browse',
                'sections': sections,
            },
        },
    }
    requests.post(f'{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages', json=payload, headers=_headers(), timeout=10)


def send_cta_url_message(to: str, body_text: str, button_text: str, url: str):
    """Send a CTA (call-to-action) button pointing to the web store."""
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'interactive',
        'interactive': {
            'type': 'cta_url',
            'body': {'text': body_text},
            'action': {'name': 'cta_url', 'parameters': {'display_text': button_text, 'url': url}},
        },
    }
    requests.post(f'{WA_API_BASE}/{WA_PHONE_NUMBER_ID}/messages', json=payload, headers=_headers(), timeout=10)
