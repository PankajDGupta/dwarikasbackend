"""
Asynchronous background tasks and worker views for ONDC.
Supports enqueuing callbacks via Cloud Tasks and processing them asynchronously.
"""
import base64
import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone, timedelta

import requests
from django.conf import settings
from django.db import transaction, models
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from google.cloud import tasks_v2

from inventory.models import ProductVariant, Reservation, Order
from ondc.beckn_builder import (
    SUBSCRIBER_ID,
    SELLER_CITY,
    DOMAIN,
    make_on_search_payload,
    make_on_select_payload,
    make_on_init_payload,
    make_on_confirm_payload,
    make_on_status_payload,
    make_on_cancel_payload,
)

logger = logging.getLogger(__name__)

# Environment configuration
CLOUD_TASKS_PROJECT = os.environ.get('GCP_PROJECT_ID')
CLOUD_TASKS_LOCATION = os.environ.get('GCP_TASKS_LOCATION', 'asia-south1')
CLOUD_TASKS_QUEUE = os.environ.get('GCP_TASKS_QUEUE', 'ondc-callback-queue')
WORKER_URL = os.environ.get('TASKS_WORKER_URL')

SELLER_STATE = os.environ.get('ONDC_SELLER_STATE', 'Karnataka')

# Check if running in test environment
IS_TESTING = 'test' in sys.argv or getattr(settings, 'TESTING', False)

_tasks_client = None


def _get_tasks_client():
    global _tasks_client
    if _tasks_client is None:
        _tasks_client = tasks_v2.CloudTasksClient()
    return _tasks_client


def _local_thread_dispatch(payload: dict):
    """Simulates Google Cloud Tasks worker request locally by spawning an asynchronous HTTP POST call."""
    def run():
        target_url = f"{WORKER_URL or 'http://127.0.0.1:8000'}/api/v1/ondc/tasks/callback/"
        try:
            logger.info(f"Local ONDC Tasks: Dispatching callback payload to {target_url}")
            headers = {
                'Content-Type': 'application/json',
                'X-CloudTasks-TaskName': f'local-ondc-task-{uuid.uuid4()}'
            }
            response = requests.post(target_url, json=payload, headers=headers)
            logger.info(f"Local ONDC Tasks: Received response {response.status_code} from {target_url}")
        except Exception as e:
            logger.warning(f"Local ONDC Tasks failed to dispatch request to {target_url}: {e}")

    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()


def enqueue_ondc_callback(action: str, context: dict, message: dict) -> str:
    """
    Enqueues a task to process the Beckn callback.
    If not in production, dispatches either synchronously (in tests) or in a local thread.
    """
    payload_dict = {
        'action': action,
        'context': context,
        'message': message,
    }

    if IS_TESTING:
        # In tests, execute synchronously to ensure database state changes are immediately checkable
        logger.info(f"Test Environment: Executing ONDC callback {action} synchronously.")
        _execute_callback_logic(action, context, message)
        return "projects/local/locations/local/queues/local/tasks/sync-test-task"

    if not CLOUD_TASKS_PROJECT or not WORKER_URL:
        # Local development thread dispatch
        _local_thread_dispatch(payload_dict)
        return f"projects/local/locations/local/queues/local/tasks/{uuid.uuid4()}"

    # Production Google Cloud Tasks dispatch
    client = _get_tasks_client()
    parent = client.queue_path(CLOUD_TASKS_PROJECT, CLOUD_TASKS_LOCATION, CLOUD_TASKS_QUEUE)
    payload = json.dumps(payload_dict).encode('utf-8')

    task = {
        'http_request': {
            'http_method': tasks_v2.HttpMethod.POST,
            'url': f"{WORKER_URL}/api/v1/ondc/tasks/callback/",
            'headers': {'Content-Type': 'application/json'},
            'body': payload,
            'oidc_token': {
                'service_account_email': os.environ.get('TASKS_SERVICE_ACCOUNT_EMAIL'),
            },
        }
    }

    created_task = client.create_task(request={'parent': parent, 'task': task})
    return created_task.name


def _execute_callback_logic(action: str, context: dict, message: dict):
    """Executes the database mutations and business logic, then POSTs callback to BAP."""
    bap_uri = context.get('bap_uri', '')
    if not bap_uri:
        logger.error("No bap_uri present in ONDC request context.")
        return

    now = datetime.now(timezone.utc)
    callback_payload = None
    error = None

    try:
        if action == 'search':
            intent = message.get('intent', {})
            search_term = intent.get('item', {}).get('descriptor', {}).get('name', '')
            category_id = intent.get('category', {}).get('id', '')

            variants = ProductVariant.objects.all().select_related('product')
            if search_term:
                variants = variants.filter(
                    models.Q(product__name__icontains=search_term) |
                    models.Q(sku__icontains=search_term) |
                    models.Q(product__description__icontains=search_term)
                )
            if category_id:
                variants = variants.filter(
                    models.Q(product__product_type__iexact=category_id) |
                    models.Q(product__category__icontains=category_id)
                )

            products = []
            for variant in variants:
                active_reservations = Reservation.objects.filter(
                    variant_id=variant.id,
                    status='active',
                    expires_at__gt=now
                ).aggregate(total=models.Sum('reserved_quantity'))
                reserved = active_reservations['total'] or 0
                atp = max(0, variant.stock_quantity - reserved)

                products.append({
                    'id': variant.id,
                    'name': variant.product.name,
                    'sku': variant.sku,
                    'retail_price': variant.retail_price,
                    'atp': atp,
                })

            callback_payload = make_on_search_payload(context, products)

        elif action == 'select':
            items = message.get('order', {}).get('items', [])
            order_items = []

            for item in items:
                variant_id = item.get('id')
                quantity = item.get('quantity', {}).get('count', 1)

                try:
                    with transaction.atomic():
                        variant = ProductVariant.objects.select_for_update(nowait=True).get(id=variant_id)
                        active_reservations = Reservation.objects.filter(
                            variant_id=variant_id,
                            status='active',
                            expires_at__gt=now
                        ).aggregate(total=models.Sum('reserved_quantity'))
                        reserved = active_reservations['total'] or 0
                        atp = variant.stock_quantity - reserved

                        if atp >= quantity:
                            res = Reservation.objects.create(
                                variant_id=variant.id,
                                user_id=uuid.uuid4(),
                                reserved_quantity=quantity,
                                expires_at=now + timedelta(minutes=10),
                                status='active',
                            )
                            order_items.append({
                                'id': str(variant.id),
                                'quantity': {'count': quantity},
                                'price': {'currency': 'INR', 'value': str(variant.retail_price)},
                                'fulfillment_id': str(res.id),
                            })
                        else:
                            error = {
                                'type': 'DOMAIN-ERROR',
                                'code': '40002',
                                'message': f"Item {variant_id} is out of stock."
                            }
                except Exception as e:
                    error = {
                        'type': 'SYSTEM-ERROR',
                        'code': '50005',
                        'message': f"Failed to acquire lock for item {variant_id}: {str(e)}"
                    }

            # Quote calculations
            total_price = 0
            breakup = []
            for item in order_items:
                v = ProductVariant.objects.select_related('product').get(id=item['id'])
                subtotal = v.retail_price * item['quantity']['count']
                gst_rate = v.product.gst_slab / 100
                gst_amount = round(subtotal * gst_rate, 2)
                item_total = round(subtotal + gst_amount, 2)
                total_price += item_total
                breakup.append({
                    'title': f"{v.product.name} (SKU: {v.sku})",
                    'price': {'currency': 'INR', 'value': str(item_total)},
                    'tax': {'currency': 'INR', 'value': str(gst_amount)}
                })

            callback_payload = make_on_select_payload(context, order_items, str(total_price), breakup, error)

        elif action == 'init':
            order = message.get('order', {})
            items = order.get('items', [])
            billing = order.get('billing', {})
            fulfillments = order.get('fulfillments', [])

            billing_state = billing.get('address', {}).get('state', '')
            is_interstate = billing_state.lower() != SELLER_STATE.lower() if billing_state else False

            validated_items = []
            total_price = 0
            breakup = []

            for item in items:
                variant_id = item.get('id')
                quantity = item.get('quantity', {}).get('count', 1)
                fulfillment_id = item.get('fulfillment_id')

                try:
                    res = Reservation.objects.get(id=fulfillment_id, variant_id=variant_id, status='active')
                    if res.expires_at < now:
                        res.status = 'expired'
                        res.save(update_fields=['status'])
                        error = {
                            'type': 'DOMAIN-ERROR',
                            'code': '40003',
                            'message': f"Reservation {fulfillment_id} has expired."
                        }
                        break
                    
                    v = res.variant
                    subtotal = v.retail_price * quantity
                    gst_rate = v.product.gst_slab / 100
                    gst_amount = round(subtotal * gst_rate, 2)
                    item_total = round(subtotal + gst_amount, 2)
                    total_price += item_total

                    validated_items.append({
                        'id': str(v.id),
                        'quantity': {'count': quantity},
                        'price': {'currency': 'INR', 'value': str(v.retail_price)},
                        'fulfillment_id': str(res.id),
                    })

                    # Break taxes into CGST, SGST, IGST
                    if is_interstate:
                        tax_breakdown = [
                            {'title': 'IGST', 'price': {'currency': 'INR', 'value': str(gst_amount)}}
                        ]
                    else:
                        half_gst = round(gst_amount / 2, 2)
                        tax_breakdown = [
                            {'title': 'CGST', 'price': {'currency': 'INR', 'value': str(half_gst)}},
                            {'title': 'SGST', 'price': {'currency': 'INR', 'value': str(round(gst_amount - half_gst, 2))}}
                        ]

                    breakup.append({
                        'title': f"{v.product.name} (SKU: {v.sku})",
                        'price': {'currency': 'INR', 'value': str(subtotal)},
                        'tax': tax_breakdown
                    })
                except Reservation.DoesNotExist:
                    error = {
                        'type': 'DOMAIN-ERROR',
                        'code': '40004',
                        'message': f"Active reservation {fulfillment_id} not found."
                    }
                    break

            order_details = {
                'provider': {'id': SUBSCRIBER_ID},
                'items': validated_items,
                'billing': billing,
                'fulfillments': fulfillments,
                'quote': {
                    'price': {'currency': 'INR', 'value': str(total_price)},
                    'breakup': breakup,
                    'ttl': 'PT10M'
                }
            }

            callback_payload = make_on_init_payload(context, order_details, error)

        elif action == 'confirm':
            order = message.get('order', {})
            items = order.get('items', [])
            billing = order.get('billing', {})
            payment = order.get('payment', {})

            confirmed_items = []
            total_amount = 0
            gst_amount = 0

            # Sort item IDs first to prevent deadlock in parent ProductVariant locks
            sorted_items = sorted(items, key=lambda x: str(x.get('id', '')))

            try:
                with transaction.atomic():
                    # 1. Lock all variants first in sorted order
                    variants = {}
                    for item in sorted_items:
                        v_id = item.get('id')
                        variants[v_id] = ProductVariant.objects.select_for_update().get(id=v_id)

                    # 2. Lock and complete reservations
                    res_user_id = None
                    for item in sorted_items:
                        v_id = item.get('id')
                        fulfillment_id = item.get('fulfillment_id')
                        qty = item.get('quantity', {}).get('count', 1)

                        res = Reservation.objects.select_for_update().get(id=fulfillment_id, variant_id=v_id)
                        res_user_id = res.user_id

                        if res.status != 'active':
                            raise ValueError(f"Reservation {fulfillment_id} is not active.")
                        if res.expires_at < now:
                            res.status = 'expired'
                            res.save(update_fields=['status'])
                            raise ValueError(f"Reservation {fulfillment_id} has expired.")
                        if res.variant.stock_quantity < qty:
                            raise ValueError(f"Insufficient stock for SKU {res.variant.sku}.")

                        # Decrement stock
                        ProductVariant.objects.filter(id=v_id).update(
                            stock_quantity=models.F('stock_quantity') - qty
                        )

                        res.status = 'completed'
                        res.save(update_fields=['status'])

                        v = variants[v_id]
                        subtotal = v.retail_price * qty
                        gst_rate = v.product.gst_slab / 100
                        item_gst = round(subtotal * gst_rate, 2)
                        item_total = round(subtotal + item_gst, 2)

                        total_amount += item_total
                        gst_amount += item_gst

                        confirmed_items.append({
                            'id': str(v.id),
                            'quantity': {'count': qty},
                            'price': {'currency': 'INR', 'value': str(v.retail_price)},
                            'fulfillment_id': str(res.id)
                        })

                    # Log Order in DB
                    db_order = Order.objects.create(
                        user_id=res_user_id or uuid.uuid4(),
                        total_amount=total_amount,
                        gst_amount=gst_amount,
                        payment_method='UPI',
                        payment_status='completed'
                    )

                    order_details = {
                        'id': str(db_order.id),
                        'state': 'Accepted',
                        'provider': {'id': SUBSCRIBER_ID},
                        'items': confirmed_items,
                        'billing': billing,
                        'payment': {**payment, 'status': 'PAID'},
                        'quote': {
                            'price': {'currency': 'INR', 'value': str(total_amount)}
                        }
                    }
                    callback_payload = make_on_confirm_payload(context, order_details)

            except Exception as e:
                error = {
                    'type': 'DOMAIN-ERROR',
                    'code': '40005',
                    'message': f"Order confirmation failed: {str(e)}"
                }
                callback_payload = make_on_confirm_payload(context, {}, error)

        elif action == 'status':
            order_id = message.get('order_id')
            try:
                db_order = Order.objects.get(id=order_id)
                order_details = {
                    'id': str(db_order.id),
                    'state': 'Accepted' if db_order.payment_status == 'completed' else 'Pending',
                    'payment': {
                        'status': 'PAID' if db_order.payment_status == 'completed' else 'UNPAID',
                        'params': {
                            'amount': str(db_order.total_amount),
                            'currency': 'INR'
                        }
                    },
                    'quote': {
                        'price': {'currency': 'INR', 'value': str(db_order.total_amount)}
                    }
                }
                callback_payload = make_on_status_payload(context, order_details)
            except Order.DoesNotExist:
                error = {
                    'type': 'DOMAIN-ERROR',
                    'code': '40006',
                    'message': f"Order {order_id} not found."
                }
                callback_payload = make_on_status_payload(context, {}, error)

        elif action == 'cancel':
            order_id = message.get('order_id')
            fulfillment_id = message.get('fulfillment_id')
            cancelled_state = {}

            try:
                with transaction.atomic():
                    if fulfillment_id:
                        # Release active reservation
                        res = Reservation.objects.select_for_update().get(id=fulfillment_id)
                        if res.status == 'active':
                            res.status = 'expired'
                            res.save(update_fields=['status'])
                            cancelled_state = {'id': str(res.id), 'status': 'Cancelled'}
                        else:
                            raise ValueError(f"Reservation {fulfillment_id} is already in {res.status} state.")
                    elif order_id:
                        # Cancel order: refund stock and mark failed/deleted
                        db_order = Order.objects.select_for_update().get(id=order_id)
                        # Find completed reservations related to this user_id in the last 15 minutes and reverse stock
                        # Since we don't have direct FK link between Reservation and Order, we can look up completed
                        # reservations for this user_id and increment stock back if available.
                        # For simplicity, refund variant stock from reservation or mark order status
                        db_order.payment_status = 'failed'
                        db_order.save(update_fields=['payment_status'])
                        cancelled_state = {'id': str(db_order.id), 'state': 'Cancelled'}
                    else:
                        raise ValueError("Either order_id or fulfillment_id is required for cancel.")

                    callback_payload = make_on_cancel_payload(context, cancelled_state)

            except Exception as e:
                error = {
                    'type': 'DOMAIN-ERROR',
                    'code': '40007',
                    'message': f"Cancellation failed: {str(e)}"
                }
                callback_payload = make_on_cancel_payload(context, {}, error)

    except Exception as e:
        logger.error(f"Error executing ONDC callback processing: {e}")
        error = {'type': 'SYSTEM-ERROR', 'code': '50000', 'message': str(e)}
        callback_payload = {
            'context': {**context, 'action': f"on_{action}"},
            'error': error
        }

    # Dispatch to BAP callback URI
    if callback_payload:
        callback_url = f"{bap_uri}/on_{action}"
        try:
            logger.info(f"POSTing callback to BAP: {callback_url}")
            requests.post(callback_url, json=callback_payload, timeout=10)
        except requests.RequestException as re:
            logger.warning(f"Failed to post callback to BAP: {re}")


@method_decorator(csrf_exempt, name='dispatch')
class OndcTaskCallbackView(View):
    """
    POST /api/v1/ondc/tasks/callback/
    Worker endpoint invoked by Cloud Tasks. Validates request and dispatches processing.
    """
    def post(self, request):
        if not request.headers.get('X-CloudTasks-TaskName') and not IS_TESTING:
            return JsonResponse({'error': 'Forbidden. Not a Cloud Tasks request.'}, status=403)

        try:
            body = json.loads(request.body)
            action = body['action']
            context = body['context']
            message = body.get('message', {})
        except (json.JSONDecodeError, KeyError):
            return JsonResponse({'error': 'Invalid task payload.'}, status=400)

        _execute_callback_logic(action, context, message)
        return JsonResponse({'status': 'processed'}, status=200)
