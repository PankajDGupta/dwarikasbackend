"""
Discount suggestion analytics service.
Queries existing inventory, order, and invoice data to score variants for discount suitability.
Called by the Cloud Tasks worker — should not be called from a synchronous request handler.
"""
import os
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone as dj_timezone

from inventory.models import ProductVariant, Reservation, InvoiceLineItem
from promotions.models import DiscountSuggestion

logger = logging.getLogger(__name__)

# Tuning constants — adjust these to calibrate sensitivity (support env overrides)
SUGGESTION_THRESHOLD_SCORE = int(os.environ.get('SUGGESTION_THRESHOLD_SCORE', 60))
MIN_STOCK_TO_ANALYSE = 5                 # Skip variants with fewer than 5 units
MIN_MARGIN_PCT_TO_SUGGEST = int(os.environ.get('MIN_MARGIN_PCT_TO_SUGGEST', 15))
ANALYSIS_LOOKBACK_DAYS = 90             # Sales window for velocity calculation
STALE_ORDER_DAYS = 30                   # "No order in N days" starts raising the score
MAX_STOCK_VELOCITY_RATIO = 10           # Stock / monthly_sales beyond this = critical


def _get_variant_cost_price(sku: str) -> Decimal | None:
    """
    Returns the most recent unit cost from confirmed invoice line items.
    Returns None if no cost data is available.
    """
    item = (
        InvoiceLineItem.objects
        .filter(
            sku=sku,
            invoice__status='confirmed',
        )
        .order_by('-invoice__created_at')
        .first()
    )
    return item.unit_price if item else None


def score_variant(variant: ProductVariant, now: datetime) -> dict | None:
    """
    Computes a discount score for a single variant.
    Returns a dict of signal values and the composite score, or None if below threshold.
    """
    # Skip very low stock — no point discounting items already nearly gone
    if variant.stock_quantity < MIN_STOCK_TO_ANALYSE:
        return None

    lookback_start = now - timedelta(days=ANALYSIS_LOOKBACK_DAYS)

    # ── Signal 1: Sales velocity ─────────────────────────────────────────────
    # Count completed orders in the lookback window that touched this variant
    orders_in_window = (
        Reservation.objects
        .filter(
            variant_id=variant.id,
            status='completed',
            expires_at__gte=lookback_start,
        )
        .count()
    )
    # Average monthly sales over the lookback period
    monthly_factor = Decimal(str(ANALYSIS_LOOKBACK_DAYS / 30))
    avg_monthly_sales = Decimal(str(orders_in_window)) / monthly_factor if monthly_factor else Decimal('0')

    # ── Signal 2: Days since last order ──────────────────────────────────────
    last_completed = (
        Reservation.objects
        .filter(variant_id=variant.id, status='completed')
        .aggregate(last=Max('expires_at'))['last']
    )
    days_since_last_order = (now - last_completed).days if last_completed else None

    # ── Signal 3: Reservation abandonment rate ───────────────────────────────
    total_reservations = Reservation.objects.filter(
        variant_id=variant.id,
        expires_at__gte=lookback_start,
    ).count()

    abandoned_reservations = Reservation.objects.filter(
        variant_id=variant.id,
        status='expired',
        expires_at__gte=lookback_start,
    ).count()

    abandonment_rate = (
        abandoned_reservations / total_reservations
        if total_reservations > 0 else 0
    )

    # ── Signal 4: Margin headroom ────────────────────────────────────────────
    cost_price = _get_variant_cost_price(variant.sku)
    if cost_price and variant.retail_price > 0:
        margin_pct = ((variant.retail_price - cost_price) / variant.retail_price) * 100
    elif variant.mrp and variant.retail_price < variant.mrp:
        # Estimate margin from MRP gap if no invoice cost available
        margin_pct = ((variant.mrp - variant.retail_price) / variant.mrp) * 100
    else:
        margin_pct = None

    # Do not suggest if margin is too thin
    if margin_pct is not None and margin_pct < MIN_MARGIN_PCT_TO_SUGGEST:
        return None

    # ── Composite Score Calculation ──────────────────────────────────────────

    # Stock excess sub-score (0–100)
    velocity_ratio = 0.0
    if avg_monthly_sales > 0:
        velocity_ratio = float(variant.stock_quantity / avg_monthly_sales)
        stock_score = min(100, int((velocity_ratio / MAX_STOCK_VELOCITY_RATIO) * 100))
    else:
        # No sales at all — maximum stock concern
        stock_score = 100 if variant.stock_quantity > MIN_STOCK_TO_ANALYSE else 50

    # Recency sub-score (0–100)
    if days_since_last_order is None:
        recency_score = 90  # Never sold
    elif days_since_last_order > STALE_ORDER_DAYS * 3:
        recency_score = 100
    elif days_since_last_order > STALE_ORDER_DAYS:
        recency_score = int((days_since_last_order / (STALE_ORDER_DAYS * 3)) * 100)
    else:
        recency_score = max(0, int((days_since_last_order / STALE_ORDER_DAYS) * 50))

    # Abandonment sub-score (0–100)
    abandonment_score = int(abandonment_rate * 100)

    # Margin sub-score (0–100) — higher margin = more room to discount = higher score
    if margin_pct is not None:
        margin_score = min(100, int(float(margin_pct) * 2))  # 50% margin → 100 score
    else:
        margin_score = 50  # Neutral when unknown

    composite = int(
        stock_score * 0.35
        + recency_score * 0.30
        + abandonment_score * 0.15
        + margin_score * 0.20
    )

    if composite < SUGGESTION_THRESHOLD_SCORE:
        return None

    # ── Priority label ────────────────────────────────────────────────────────
    if composite >= 80:
        priority = 'critical'
    elif composite >= 60:
        priority = 'high'
    else:
        priority = 'medium'

    # ── Reason summary ────────────────────────────────────────────────────────
    reason_parts = []
    if days_since_last_order is None:
        reason_parts.append('No sales recorded')
    elif days_since_last_order > STALE_ORDER_DAYS:
        reason_parts.append(f'No order in {days_since_last_order} days')
    if avg_monthly_sales < 1:
        reason_parts.append('Near-zero monthly sales')
    if stock_score >= 70:
        reason_parts.append(f'High stock: {variant.stock_quantity} units '
                            f'({velocity_ratio:.1f}x monthly sales)')
    if abandonment_rate > 0.4:
        reason_parts.append(f'High cart abandonment ({int(abandonment_rate*100)}%)')

    reason_summary = ' · '.join(reason_parts) if reason_parts else 'Slow-moving inventory detected'

    # ── Suggested discount ────────────────────────────────────────────────────
    if margin_pct is not None:
        m = Decimal(str(margin_pct))
        if composite >= 80:
            disc_pct = min(Decimal('25'), m * Decimal('0.7'))
        else:
            disc_pct = min(Decimal('15'), m * Decimal('0.5'))
    else:
        disc_pct = Decimal('10')

    disc_pct = max(Decimal('5'), round(disc_pct, 0))

    return {
        'discount_score': composite,
        'priority': priority,
        'reason_summary': reason_summary,
        'reasons': {
            'stock_score': stock_score,
            'recency_score': recency_score,
            'abandonment_score': abandonment_score,
            'margin_score': margin_score,
        },
        'suggested_discount_type': 'percentage',
        'suggested_discount_value': disc_pct,
        'suggested_ends_days': 7 if composite >= 80 else 14,
        'current_stock': variant.stock_quantity,
        'avg_monthly_sales': avg_monthly_sales,
        'days_since_last_order': days_since_last_order,
        'cost_price': cost_price,
        'margin_pct': margin_pct,
    }


@transaction.atomic
def run_discount_analysis():
    """
    Main entry point for the analytics job.
    Scores all eligible variants, upserts DiscountSuggestion records,
    and expires stale pending suggestions that are no longer relevant.
    """
    now = dj_timezone.now()
    logger.info('Starting discount analysis job...')

    variants = (
        ProductVariant.objects
        .select_related('product')
        .filter(stock_quantity__gte=MIN_STOCK_TO_ANALYSE)
    )

    surfaced_variant_ids = []
    new_count = 0
    updated_count = 0

    for variant in variants:
        try:
            result = score_variant(variant, now)
        except Exception as exc:
            logger.error(f'Scoring failed for variant {variant.id}: {exc}')
            continue

        if result is None:
            continue

        surfaced_variant_ids.append(variant.id)

        existing = DiscountSuggestion.objects.filter(
            variant_id=variant.id,
            status='pending',
        ).first()

        if existing:
            # Update score and signals on existing pending suggestion
            for field, value in result.items():
                setattr(existing, field, value)
            existing.analysed_at = now
            existing.save()
            updated_count += 1
        else:
            # Check if recently dismissed — honour the dismissed_until window
            dismissed = DiscountSuggestion.objects.filter(
                variant_id=variant.id,
                status='dismissed',
                dismissed_until__gt=now,
            ).exists()
            if dismissed:
                continue

            DiscountSuggestion.objects.create(
                variant_id=variant.id,
                product_id=variant.product_id,
                analysed_at=now,
                **result,
            )
            new_count += 1

    # Expire pending suggestions for variants that no longer score above threshold
    expired_count = DiscountSuggestion.objects.filter(
        status='pending',
    ).exclude(
        variant_id__in=surfaced_variant_ids,
    ).update(status='expired')

    logger.info(
        f'Discount analysis complete: {new_count} new, {updated_count} updated, '
        f'{expired_count} expired suggestions.'
    )
    return {
        'new': new_count,
        'updated': updated_count,
        'expired': expired_count,
    }
