import hashlib
import hmac
import json
import os

from django.http import JsonResponse, HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from whatsapp.intent_parser import classify_intent
from whatsapp.handlers import (
    handle_catalog, handle_stock_check,
    handle_order_status, handle_fallback,
)

WA_VERIFY_TOKEN = os.environ.get('WA_VERIFY_TOKEN')
WA_APP_SECRET = os.environ.get('WA_APP_SECRET')


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppWebhookView(View):

    def get(self, request):
        """Meta webhook verification challenge."""
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')

        if mode == 'subscribe' and token == WA_VERIFY_TOKEN:
            return HttpResponse(challenge, content_type='text/plain', status=200)
        return HttpResponse('Verification failed.', status=403)

    def post(self, request):
        """Process inbound WhatsApp messages."""
        if not self._verify_payload_signature(request):
            return JsonResponse({'error': 'Invalid signature.'}, status=401)

        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        # Extract message from Meta webhook structure
        try:
            entry = body['entry'][0]
            change = entry['changes'][0]
            value = change['value']
            message = value['messages'][0]
            sender = message['from']
            message_text = message.get('text', {}).get('body', '')
        except (KeyError, IndexError):
            # Delivery receipts, status updates — not action messages
            return JsonResponse({'status': 'ignored'}, status=200)

        # Route to intent handler
        intent = classify_intent(message_text)
        if intent == 'catalog':
            handle_catalog(sender)
        elif intent == 'stock_check':
            handle_stock_check(sender, message_text)
        elif intent == 'order_status':
            handle_order_status(sender, sender)
        else:
            handle_fallback(sender)

        return JsonResponse({'status': 'ok'}, status=200)

    def _verify_payload_signature(self, request) -> bool:
        """
        Verifies the X-Hub-Signature-256 header to confirm the request is from Meta.
        """
        if not WA_APP_SECRET:
            return True   # Skip verification in local dev
        signature_header = request.headers.get('X-Hub-Signature-256', '')
        if not signature_header.startswith('sha256='):
            return False
        expected = 'sha256=' + hmac.new(
            WA_APP_SECRET.encode(), request.body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature_header, expected)
