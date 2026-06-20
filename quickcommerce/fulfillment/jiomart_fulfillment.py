from decimal import Decimal
import logging
from django.db import transaction
from quickcommerce.models import QCWarehouseMapping
from inventory.models import Order

logger = logging.getLogger(__name__)

def process_jiomart_order(payload: dict) -> dict:
    """
    Simulates fetching details for a new JioMart order, mapping it
    to a facility, and setting up an internal fulfillment record.
    """
    order_id = payload.get("order_id")
    location_id = payload.get("location_id")
    total_amount = payload.get("total_amount", 0.0)

    # Resolve JioMart location to facility mapping
    facility_code = None
    mapping = QCWarehouseMapping.objects.filter(
        platform='jiomart',
        platform_location_id=location_id,
        mapping_type='facility',
        is_active=True
    ).first()
    if mapping:
        facility_code = mapping.internal_facility_code

    logger.info(f"JioMart Fulfillment: Order {order_id} mapped to facility {facility_code}.")

    # In a full flow, we would register the order in public.orders
    # For simulation/mock, we get or create the order
    order, created = Order.objects.get_or_create(
        id=order_id,
        defaults={
            "total_amount": total_amount,
            "gst_amount": total_amount * Decimal("0.18"),  # assume 18% default
            "payment_method": "online",
            "payment_status": "completed",
            "carrier_status": "staged"
        }
    )

    return {
        "order_id": str(order.id),
        "facility_code": facility_code,
        "status": order.carrier_status,
        "shipping_label_url": "https://cdn.jiomart.com/labels/mock-label.pdf"
    }


def close_jiomart_manifest(order_uuid: str, manifest_id: str) -> dict:
    """
    Closes the manifest for a JioMart order and transitions order status to 'picked_up'.
    """
    try:
        order = Order.objects.get(id=order_uuid)
    except Order.DoesNotExist:
        raise ValueError(f"JioMart order '{order_uuid}' not found.")

    with transaction.atomic():
        order.carrier_status = "picked_up"
        order.tracking_reference = manifest_id
        order.save()

    logger.info(f"JioMart Manifest closed for order {order_uuid} with manifest {manifest_id}.")

    return {
        "success": True,
        "order_id": str(order.id),
        "carrier_status": order.carrier_status,
        "tracking_reference": order.tracking_reference
    }
