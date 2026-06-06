"""
Views for the ONDC Seller Node (Beckn Protocol).
Conform to Beckn Protocol v1.2.5.
All endpoints are CSRF-exempt and return HTTP 202 immediately,
dispatching BAP callbacks asynchronously via Cloud Tasks.
"""
import json
import logging
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ondc.beckn_auth import verify_beckn_signature
from ondc.beckn_builder import make_ack_response
from ondc.tasks import enqueue_ondc_callback

logger = logging.getLogger(__name__)


class BaseOndcView(View):
    """Base View that handles standard Beckn validation, logging, and enqueuing."""
    action_name = None

    def post(self, request, *args, **kwargs):
        if not self.action_name:
            return JsonResponse({'error': 'Action name not configured.'}, status=500)

        # 1. Verify Beckn Signature
        if not verify_beckn_signature(request):
            return JsonResponse({'error': 'Invalid Beckn signature.'}, status=401)

        # 2. Parse request payload
        try:
            body = json.loads(request.body)
            context = body['context']
            message = body.get('message', {})
            # Double check that context.action matches the expected endpoint action
            if context.get('action') != self.action_name:
                return JsonResponse({'error': f"Context action '{context.get('action')}' does not match endpoint action '{self.action_name}'."}, status=400)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Malformed ONDC request payload: {e}")
            return JsonResponse({'error': 'Malformed Beckn payload.'}, status=400)

        # 3. Build synchronous ACK response
        ack = make_ack_response(context)

        # 4. Enqueue the asynchronous callback task
        enqueue_ondc_callback(self.action_name, context, message)

        # 5. Return HTTP 202 immediately
        return JsonResponse(ack, status=202)


@method_decorator(csrf_exempt, name='dispatch')
class OndcSearchView(BaseOndcView):
    """
    POST /api/v1/ondc/search/
    Discovery — catalogue lookup with ATP checks.
    """
    action_name = 'search'


@method_decorator(csrf_exempt, name='dispatch')
class OndcSelectView(BaseOndcView):
    """
    POST /api/v1/ondc/select/
    Item selection & active stock reservation hold.
    """
    action_name = 'select'


@method_decorator(csrf_exempt, name='dispatch')
class OndcInitView(BaseOndcView):
    """
    POST /api/v1/ondc/init/
    Billing/Shipping info initialisation + GST CGST/SGST/IGST tax calculation.
    """
    action_name = 'init'


@method_decorator(csrf_exempt, name='dispatch')
class OndcConfirmView(BaseOndcView):
    """
    POST /api/v1/ondc/confirm/
    Commit order & decrement variant stock.
    """
    action_name = 'confirm'


@method_decorator(csrf_exempt, name='dispatch')
class OndcStatusView(BaseOndcView):
    """
    POST /api/v1/ondc/status/
    Query order payment/fulfillment status.
    """
    action_name = 'status'


@method_decorator(csrf_exempt, name='dispatch')
class OndcCancelView(BaseOndcView):
    """
    POST /api/v1/ondc/cancel/
    Release reservation or cancel confirmed order.
    """
    action_name = 'cancel'
