"""
Promotion resolution service.
Finds the best active promotion for a given ProductVariant.
Used by checkout (Spec 07) and product catalog (Spec 06) to compute effective price.
"""
from decimal import Decimal
from django.db import models
from django.utils import timezone
from promotions.models import Promotion, PromotionItem


from collections import defaultdict

def build_active_promotions_cache() -> dict:
    """
    Builds a pre-fetched cache dictionary of active promotions and their items:
    {
        'variant': {variant_id: [PromotionItem]},
        'product': {product_id: [PromotionItem]}
    }
    This is used to prevent N+1 queries during bulk serialization of variants/products.
    """
    now = timezone.now()
    live_promotions = Promotion.objects.filter(
        is_active=True,
        starts_at__lte=now,
    ).filter(
        models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now)
    )

    matching_items = PromotionItem.objects.filter(
        promotion__in=live_promotions
    ).select_related('promotion')

    cache = {
        'variant': defaultdict(list),
        'product': defaultdict(list)
    }

    for item in matching_items:
        if item.variant_id:
            cache['variant'][item.variant_id].append(item)
        if item.product_id:
            cache['product'][item.product_id].append(item)

    return cache


def get_active_promotion_for_variant(variant, active_promotions_cache=None) -> dict | None:
    """
    Returns the best applicable promotion for a variant, or None.
    'Best' means the promotion that yields the lowest effective price.

    Returns a dict:
    {
        'promotion_id': uuid,
        'title': str,
        'discount_type': str,
        'discount_value': Decimal,
        'effective_price': Decimal,
        'savings': Decimal,
    }
    """
    if active_promotions_cache is not None:
        matching_items = (
            active_promotions_cache['variant'].get(variant.id, []) +
            active_promotions_cache['product'].get(variant.product_id, [])
        )
    else:
        now = timezone.now()
        # Fetch all currently active promotions that match this variant or its parent product
        live_promotions = Promotion.objects.filter(
            is_active=True,
            starts_at__lte=now,
        ).filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now)
        )

        # Promotions targeting this variant directly OR the variant's parent product
        matching_items = PromotionItem.objects.filter(
            promotion__in=live_promotions
        ).filter(
            models.Q(variant_id=variant.id) | models.Q(product_id=variant.product_id)
        ).select_related('promotion')

    best = None
    best_price = variant.retail_price

    for item in matching_items:
        promo = item.promotion
        price = variant.retail_price
        if promo.discount_type == 'percentage':
            discount = price * (promo.discount_value / Decimal('100'))
            if promo.max_discount_cap:
                discount = min(discount, promo.max_discount_cap)
            effective = price - discount
        else:  # flat_amount
            effective = max(price - promo.discount_value, Decimal('0'))

        if effective < best_price:
            best_price = effective
            best = {
                'promotion_id': str(promo.id),
                'title': promo.title,
                'discount_type': promo.discount_type,
                'discount_value': str(promo.discount_value),
                'effective_price': effective,
                'savings': price - effective,
            }

    return best
