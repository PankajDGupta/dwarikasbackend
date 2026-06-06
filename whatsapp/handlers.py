"""
Intent-specific message handlers. Each handler queries Supabase and
dispatches a formatted WhatsApp response back to the customer.
"""
from datetime import datetime, timezone

from inventory.models import ProductVariant, Reservation, Order
from whatsapp.wa_client import send_text_message, send_list_message, send_cta_url_message
from whatsapp.intent_parser import extract_sku_from_message

WEB_STORE_URL = 'https://dwarikas.com/shop'


def handle_catalog(sender: str):
    """Return a list message with the top 10 products by name."""
    variants = ProductVariant.objects.select_related('product').order_by('product__name')[:10]
    sections = [
        {
            'title': 'Our Products',
            'rows': [
                {
                    'id': str(v.id),
                    'title': v.product.name[:24],   # WhatsApp title max 24 chars
                    'description': f'Rs. {v.retail_price} | SKU: {v.sku}',
                }
                for v in variants
            ],
        }
    ]
    send_list_message(
        to=sender,
        header_text='Dwarikas Catalogue',
        body_text='Here are our latest products. Tap an item for details.',
        sections=sections,
    )


def handle_stock_check(sender: str, message_text: str):
    """Return current ATP stock for a given SKU."""
    sku = extract_sku_from_message(message_text)
    if not sku:
        send_text_message(sender, "Please share the SKU code. Example: 'check stock SKU-001'")
        return

    try:
        variant = ProductVariant.objects.select_related('product').get(sku__iexact=sku)
    except ProductVariant.DoesNotExist:
        send_text_message(sender, f"SKU *{sku}* was not found in our catalogue.")
        return

    now = datetime.now(timezone.utc)
    reserved = sum(
        r.reserved_quantity for r in Reservation.objects.filter(
            variant_id=variant.id, status='active', expires_at__gt=now
        )
    )
    atp = max(0, variant.stock_quantity - reserved)
    status_text = "✅ In Stock" if atp > 0 else "❌ Out of Stock"

    send_text_message(
        sender,
        f"*{variant.product.name}* ({sku})\n"
        f"Price: Rs. {variant.retail_price}\n"
        f"Availability: {status_text} ({atp} units available)\n\n"
        f"Shop online 👇",
    )
    send_cta_url_message(sender, '', 'View Product', f"{WEB_STORE_URL}/product/{variant.product_id}")


def handle_order_status(sender: str, user_phone: str):
    """Look up the caller's most recent order by phone number (via profile lookup)."""
    send_text_message(
        sender,
        "To track your order, please visit our website or share your Order ID.\n"
        f"Visit: {WEB_STORE_URL}/orders",
    )


def handle_fallback(sender: str):
    """Default menu for unrecognised intents."""
    send_text_message(
        sender,
        "Hi! I'm the Dwarikas assistant. I can help you with:\n\n"
        "1️⃣ *catalog* — Browse our products\n"
        "2️⃣ *check stock SKU* — Check availability\n"
        "3️⃣ *order status* — Track your order\n\n"
        "Just type one of the above to get started!",
    )
