import hmac
import hashlib
import logging
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from quickcommerce.models import QCPurchaseOrder, QCPOLineItem, QCPlatformCredentials, QCWarehouseMapping
from inventory.models import ProductVariant

import datetime
import uuid

logger = logging.getLogger(__name__)

def verify_blinkit_webhook_signature(body_bytes: bytes, incoming_signature: str, secret: str) -> bool:
    """
    Verifies that the incoming Blinkit webhook signature is valid.
    """
    if not secret or not incoming_signature:
        return False
    computed = hmac.new(secret.encode('utf-8'), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, incoming_signature)


def sanitize_decimal(obj):
    if isinstance(obj, dict):
        return {k: sanitize_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_decimal(x) for x in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    elif isinstance(obj, uuid.UUID):
        return str(obj)
    return obj


def process_blinkit_po(payload: dict) -> QCPurchaseOrder:
    """
    Ingests and validates a Blinkit B2B PO.
    Resolves the delivery pincode to a facility code.
    Performs MRP parity checks.
    """
    sanitized_payload = sanitize_decimal(payload)
    po_id = sanitized_payload.get("po_id")
    vendor_id = sanitized_payload.get("vendor_id")
    pincode = sanitized_payload.get("delivery_pincode")
    items = sanitized_payload.get("items", [])

    # 1. Resolve pincode to warehouse facility code
    facility_code = None
    mapping = QCWarehouseMapping.objects.filter(
        platform='blinkit',
        platform_location_id=pincode,
        mapping_type='pincode',
        is_active=True
    ).first()
    if mapping:
        facility_code = mapping.internal_facility_code

    mrp_mismatch_detected = False
    mismatch_details = []

    # 2. Ingest PO atomically
    with transaction.atomic():
        po, created = QCPurchaseOrder.objects.get_or_create(
            platform_po_id=po_id,
            defaults={
                "platform": "blinkit",
                "vendor_id": vendor_id,
                "facility_code": facility_code,
                "raw_payload": sanitized_payload,
                "po_status": "RECEIVED"
            }
        )
        if not created:
            return po

        total_amount = Decimal("0.00")

        # 3. Process lines and check MRP parity
        for item in items:
            sku = item.get("sku")
            upc = item.get("upc")
            ordered_qty = item.get("ordered_quantity", 0)
            unit_price = Decimal(str(item.get("unit_price", 0.0)))
            po_mrp = Decimal(str(item.get("mrp", 0.0)))

            total_amount += unit_price * ordered_qty

            # Find matching variant
            variant = ProductVariant.objects.filter(barcode=upc).first()
            if not variant:
                variant = ProductVariant.objects.filter(sku=sku).first()

            # Enforce MRP Parity
            if variant:
                if Decimal(str(variant.mrp)) != po_mrp:
                    mrp_mismatch_detected = True
                    mismatch_details.append(
                        f"SKU {sku}: WMS MRP={variant.mrp} vs PO MRP={po_mrp}"
                    )

            QCPOLineItem.objects.create(
                po=po,
                variant=variant,
                platform_sku=sku,
                ordered_quantity=ordered_qty,
                unit_price=unit_price,
                mrp=po_mrp
            )

        po.total_amount = total_amount
        if mrp_mismatch_detected:
            # Block state: record mismatch in payload or flags (we'll raise an alert in view)
            # In our implementation we can store this inside po status or raw_payload
            po.raw_payload["mrp_mismatch"] = True
            po.raw_payload["mismatch_details"] = mismatch_details
        else:
            po.po_status = "VERIFIED"
        po.save()

    return po


def submit_blinkit_asn(po_id: str, dispatched_items: list, tracking_reference: str = None) -> QCPurchaseOrder:
    """
    Submits Advanced Shipping Note (ASN) for a Blinkit PO.
    Blocks dispatch if there is an MRP mismatch.
    """
    try:
        po = QCPurchaseOrder.objects.get(platform_po_id=po_id, platform='blinkit')
    except QCPurchaseOrder.DoesNotExist:
        raise ValueError(f"Purchase Order '{po_id}' not found.")

    if po.po_status in ["ASN_SENT", "INWARDED"]:
        return po

    # Check for blocked state due to MRP mismatch
    if po.raw_payload and po.raw_payload.get("mrp_mismatch"):
        raise ValueError(f"Fulfillment blocked due to MRP mismatch: {', '.join(po.raw_payload.get('mismatch_details', []))}")

    with transaction.atomic():
        # Update delivered quantities on lines
        for disp in dispatched_items:
            sku = disp.get("sku")
            qty = disp.get("dispatched_quantity", 0)
            
            line = QCPOLineItem.objects.filter(po=po, platform_sku=sku).first()
            if line:
                line.delivered_quantity = qty
                line.save()

        # Update PO status
        po.po_status = "ASN_SENT"
        po.asn_reference = f"ASN-DWR-{timezone.now().strftime('%Y%m%d')}-{po.id.hex[:6].upper()}"
        po.dispatched_at = timezone.now()
        po.save()

    return po
