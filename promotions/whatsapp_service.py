"""
WhatsApp broadcast service for promotions.
Sends a CTA message for a promotion to a list of phone numbers.
"""
import logging
import os
from decimal import Decimal
from typing import List

from promotions.models import Promotion, PromotionBroadcast
from whatsapp.wa_client import send_cta_url_message

logger = logging.getLogger(__name__)
STORE_BASE_URL = os.environ.get('STORE_BASE_URL', 'https://dwarikas.com/shop')


def build_promotion_message(promotion: Promotion, custom_message: str = None) -> str:
    """
    Builds the WhatsApp message body for a promotion.
    Uses custom_message if provided, otherwise generates a default from the promotion fields.
    """
    if custom_message:
        return custom_message

    if promotion.discount_type == 'percentage':
        discount_str = f"{promotion.discount_value:.0f}% off"
        if promotion.max_discount_cap:
            discount_str += f" (up to ₹{promotion.max_discount_cap:.0f})"
    else:
        discount_str = f"₹{promotion.discount_value:.0f} off"

    if promotion.ends_at:
        # Cross-platform date formatting (avoids Windows %-d vs Linux %-d differences)
        day = promotion.ends_at.day
        month_year = promotion.ends_at.strftime("%b %Y")
        validity = f"{day} {month_year}"
        time_line = f"⏰ Offer valid until: {validity}"
    else:
        time_line = "⏰ While stocks last!"

    parts = [
        f"🎉 *{promotion.title}*",
        "",
    ]
    if promotion.description:
        parts.append(promotion.description)
        parts.append("")

    parts += [
        f"💰 Save {discount_str} on selected items!",
        time_line,
        "",
        "Shop now and grab the deal before it's gone! 👇",
    ]
    return "\n".join(parts)


def broadcast_promotion_to_whatsapp(
    promotion: Promotion,
    phone_numbers: List[str],
    sent_by,
    message_override: str = None,
    store_url: str = None,
) -> dict:
    """
    Sends a WhatsApp CTA message for the given promotion to every phone number in the list.
    Records each attempt in PromotionBroadcast.

    Returns:
    {
        'total_sent': int,
        'total_failed': int,
        'results': [{'phone': str, 'status': 'sent'|'failed', 'error': str|None}]
    }
    """
    base_url = store_url or STORE_BASE_URL
    cta_url = f"{base_url}/promotions/{promotion.id}"
    message_body = build_promotion_message(promotion, message_override)

    results = []
    sent = 0
    failed = 0

    for phone in phone_numbers:
        try:
            send_cta_url_message(
                to=phone,
                body_text=message_body,
                button_text="Shop the Sale",
                url=cta_url,
            )
            PromotionBroadcast.objects.create(
                promotion=promotion,
                phone_number=phone,
                status='sent',
                sent_by=sent_by,
            )
            results.append({'phone': phone, 'status': 'sent', 'error': None})
            sent += 1
        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"WhatsApp broadcast failed for {phone} on promotion {promotion.id}: {error_msg}")
            PromotionBroadcast.objects.create(
                promotion=promotion,
                phone_number=phone,
                status='failed',
                failure_reason=error_msg,
                sent_by=sent_by,
            )
            results.append({'phone': phone, 'status': 'failed', 'error': error_msg})
            failed += 1

    return {
        'total_sent': sent,
        'total_failed': failed,
        'results': results,
    }
