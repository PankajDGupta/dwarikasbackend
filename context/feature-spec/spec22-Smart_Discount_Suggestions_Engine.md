# Spec 22 — Smart Discount Suggestions Engine

## Goal

Run a periodic background analytics job that queries existing inventory, sales, and cost data to identify product variants that are strong candidates for a discount promotion. Surfaced as an actionable **"Suggestions" queue** on the manager's dashboard, each suggestion can be activated as a live promotion with a single click — zero manual configuration needed.

---

## Feasibility Assessment: Can This Be Done with the Current Backend?

**Yes — entirely with data already in the database.** No external analytics service or ML platform is required.

The following signals are already captured and are sufficient to score discount candidates:

| Signal | Source Table | What It Indicates |
|---|---|---|
| Current stock level | `product_variants.stock_quantity` | High stock = possible overstocking |
| Sales in last 30/60/90 days | `orders` + join on `reservations` | Low orders relative to stock = slow mover |
| Days since last order | `orders.created_at` per variant | Long gap = stale/unsold inventory |
| Unit cost of goods | `invoice_line_items.unit_price` (confirmed invoices) | Margin headroom for a discount |
| MRP vs retail gap | `product_variants.mrp` vs `retail_price` | Existing markdown room |
| Packaging output age | `packaging_job_outputs.created_at` | Repackaged stock that hasn't moved |
| Reservation conversion rate | `reservations` status breakdown | High abandonment = price resistance signal |

**Async infrastructure:** Google Cloud Tasks is already used by Spec 10 (Invoice OCR) and Spec 13 (ONDC). The same Cloud Tasks queue can dispatch a scheduled analytics job.

**No new external dependencies required.**

---

## Architecture

```
Cloud Scheduler (cron)
       │  fires every night at 2 AM IST
       ▼
POST /api/v1/tasks/run-discount-analysis/   (Cloud Tasks internal endpoint)
       │
       ▼
DiscountAnalysisWorker
  ├── queries ProductVariant + Orders + InvoiceLineItems
  ├── scores each variant with a composite discount_score (0–100)
  ├── filters: score >= SUGGESTION_THRESHOLD (default: 60)
  ├── upserts DiscountSuggestion records
  └── marks stale suggestions as 'expired'

       │
       ▼
Manager Dashboard
GET /api/v1/promotions/suggestions/
  ├── lists pending suggestions with score, reasoning, and suggested discount
  └── POST /api/v1/promotions/suggestions/<id>/approve/
            ├── creates a Promotion record (Spec 19) from suggestion config
            └── marks suggestion as 'approved'
```

---

## Scoring Algorithm

Each variant receives a **composite discount score (0–100)** computed from weighted sub-signals. All sub-scores are normalised to 0–100 individually.

### Sub-signal Weights

| Signal | Weight | Logic |
|---|---|---|
| **Stock excess ratio** | 35% | `stock_quantity / avg_monthly_sales` — higher ratio = more overstock |
| **Sales recency** | 30% | Days since last completed order — the older, the higher this score |
| **Reservation abandonment rate** | 15% | `(expired + no-order reservations) / total reservations` — high abandonment = price resistance |
| **Margin headroom** | 20% | `(retail_price - cost_price) / retail_price` — only suggest if margin > 15% |

### Score Interpretation

| Score Range | Suggestion Priority | Label |
|---|---|---|
| 80–100 | 🔴 Critical | "Overstocked & Stagnant — urgent clearance recommended" |
| 60–79 | 🟠 High | "Slow-moving — consider a limited-time offer" |
| 40–59 | 🟡 Medium | "Moderate velocity dip — monitor or bundle" |
| < 40 | _(not surfaced)_ | Healthy — no action needed |

### Suggested Discount Calculation

The engine auto-suggests a discount value based on the score and margin:

```python
def suggest_discount(score: int, margin_pct: Decimal) -> dict:
    """
    Returns suggested discount type and value.
    Never suggests a discount that would take effective_price below cost.
    """
    if score >= 80:
        # Aggressive clearance — up to 25% of margin
        pct = min(Decimal('25'), margin_pct * Decimal('0.7'))
    elif score >= 60:
        # Moderate nudge — up to 15% of margin
        pct = min(Decimal('15'), margin_pct * Decimal('0.5'))
    else:
        pct = Decimal('10')

    return {
        'discount_type': 'percentage',
        'discount_value': round(pct, 2),
        'suggested_ends_days': 7 if score >= 80 else 14,
    }
```

---

## Relationship to Existing Specs

| Spec | Impact |
|---|---|
| **Spec 06** (Product Catalog API) | `ProductVariant` and `Product` queried for stock and pricing |
| **Spec 07/08** (Checkout + Orders) | `Reservation` and `Order` tables queried for sales velocity and abandonment |
| **Spec 10/11/12** (Invoice Pipeline) | `InvoiceLineItem.unit_price` from confirmed invoices used as cost-of-goods |
| **Spec 10** (Cloud Tasks) | Existing `tasks_service.py` pattern reused to dispatch the analytics job |
| **Spec 19** (Promotions) | `DiscountSuggestion.approve()` creates a `Promotion` record |

---

## Scope

Extends the existing `promotions/` app. No new Django app needed.

### New Endpoints

| Method | Path | Actor | Description |
|---|---|---|---|
| `GET` | `/api/v1/promotions/suggestions/` | Manager | List pending discount suggestions with score and reasoning |
| `GET` | `/api/v1/promotions/suggestions/<uuid:id>/` | Manager | Single suggestion detail |
| `POST` | `/api/v1/promotions/suggestions/<uuid:id>/approve/` | Manager | One-click: create a live Promotion from this suggestion |
| `POST` | `/api/v1/promotions/suggestions/<uuid:id>/dismiss/` | Manager | Dismiss a suggestion (won't resurface for 30 days) |
| `POST` | `/api/v1/tasks/run-discount-analysis/` | Internal (Cloud Tasks) | Trigger the analytics scoring job |

---

## Data Models

### `DiscountSuggestion`

```
id                      UUID        PK — auto
variant_id              UUID (FK)   → ProductVariant
product_id              UUID (FK)   → Product (denormalised for easy listing)
discount_score          INTEGER     0–100 composite score
priority                TEXT        'critical' | 'high' | 'medium'
reason_summary          TEXT        Human-readable explanation (e.g., "Stock: 120 units, 0 orders in 45 days")
reasons                 JSONB       Structured breakdown: { stock_score, recency_score, abandonment_score, margin_score }
suggested_discount_type TEXT        'percentage' | 'flat_amount'
suggested_discount_value DECIMAL(10,2)  Auto-computed suggested discount
suggested_ends_days     INTEGER     Suggested promotion duration in days
current_stock           INTEGER     Snapshot at analysis time
avg_monthly_sales       DECIMAL(10,2) Average units sold per month (trailing 90 days)
days_since_last_order   INTEGER     Days since the last completed order for this variant
cost_price              DECIMAL(12,2) Nullable — from latest confirmed InvoiceLineItem
margin_pct              DECIMAL(5,2) Nullable — (retail - cost) / retail × 100
status                  TEXT        'pending' | 'approved' | 'dismissed' | 'expired'
dismissed_until         TIMESTAMPTZ Nullable — don't resurface before this date
approved_promotion_id   UUID (FK)   Nullable → Promotion (set when approved)
analysed_at             TIMESTAMPTZ When this suggestion was last computed
created_at              TIMESTAMPTZ Auto
```

```python
class DiscountSuggestion(models.Model):
    PRIORITY_CHOICES = [
        ('critical', 'Critical'),
        ('high', 'High'),
        ('medium', 'Medium'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('dismissed', 'Dismissed'),
        ('expired', 'Expired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    variant = models.ForeignKey(
        'inventory.ProductVariant',
        on_delete=models.CASCADE,
        related_name='discount_suggestions',
        db_column='variant_id',
    )
    product = models.ForeignKey(
        'inventory.Product',
        on_delete=models.CASCADE,
        related_name='discount_suggestions',
        db_column='product_id',
    )
    discount_score = models.IntegerField()
    priority = models.TextField(choices=PRIORITY_CHOICES)
    reason_summary = models.TextField()
    reasons = models.JSONField(default=dict)
    suggested_discount_type = models.TextField()
    suggested_discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    suggested_ends_days = models.IntegerField(default=14)
    current_stock = models.IntegerField()
    avg_monthly_sales = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    days_since_last_order = models.IntegerField(null=True, blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    margin_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    status = models.TextField(choices=STATUS_CHOICES, default='pending')
    dismissed_until = models.DateTimeField(null=True, blank=True)
    approved_promotion = models.ForeignKey(
        'promotions.Promotion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='approved_promotion_id',
        related_name='source_suggestion',
    )
    analysed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'discount_suggestions'
        ordering = ['-discount_score', '-analysed_at']
```

---

## Files to Create / Modify

### `promotions/analytics_service.py` — Core scoring engine

```python
"""
Discount suggestion analytics service.
Queries existing inventory, order, and invoice data to score variants for discount suitability.
Called by the Cloud Tasks worker — should not be called from a synchronous request handler.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum, Avg, Q, Max
from django.utils import timezone as dj_timezone

from inventory.models import ProductVariant, Reservation, Order
from inventory.models import InvoiceLineItem, PurchaseInvoice
from promotions.models import DiscountSuggestion

logger = logging.getLogger(__name__)

# Tuning constants — adjust these to calibrate sensitivity
SUGGESTION_THRESHOLD_SCORE = 60          # Minimum score to surface a suggestion
MIN_STOCK_TO_ANALYSE = 5                 # Skip variants with fewer than 5 units
MIN_MARGIN_PCT_TO_SUGGEST = 15           # Only suggest if gross margin >= 15%
ANALYSIS_LOOKBACK_DAYS = 90             # Sales window for velocity calculation
STALE_ORDER_DAYS = 30                   # "No order in N days" starts raising the score
MAX_STOCK_VELOCITY_RATIO = 10           # Stock / monthly_sales beyond this = critical


def _get_variant_cost_price(variant_id) -> Decimal | None:
    """
    Returns the most recent unit cost from confirmed invoice line items.
    Returns None if no cost data is available.
    """
    item = (
        InvoiceLineItem.objects
        .filter(
            sku=ProductVariant.objects.get(id=variant_id).sku,
            invoice__status='confirmed',
        )
        .select_related('invoice')
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
    # Note: Order model doesn't have variant_id directly — we trace through Reservation
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
    monthly_factor = ANALYSIS_LOOKBACK_DAYS / 30
    avg_monthly_sales = Decimal(str(orders_in_window / monthly_factor)) if monthly_factor else Decimal('0')

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
    cost_price = _get_variant_cost_price(variant.id)
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
                            f'({velocity_ratio:.1f}× monthly sales)')
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
```

---

### `promotions/suggestion_views.py` — Manager endpoints

```python
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsManager
from promotions.models import DiscountSuggestion, Promotion, PromotionItem
from promotions.serializers import DiscountSuggestionSerializer


class DiscountSuggestionListView(generics.ListAPIView):
    """
    GET /api/v1/promotions/suggestions/
    Lists all pending discount suggestions ordered by score descending.
    Supports ?priority=critical|high|medium filter.
    """
    serializer_class = DiscountSuggestionSerializer
    permission_classes = [IsManager]

    def get_queryset(self):
        qs = DiscountSuggestion.objects.filter(
            status='pending',
        ).select_related('variant__product')

        priority = self.request.query_params.get('priority')
        if priority in ('critical', 'high', 'medium'):
            qs = qs.filter(priority=priority)

        return qs.order_by('-discount_score', '-analysed_at')


class DiscountSuggestionDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/promotions/suggestions/<uuid:id>/
    """
    serializer_class = DiscountSuggestionSerializer
    permission_classes = [IsManager]
    queryset = DiscountSuggestion.objects.select_related('variant__product').all()
    lookup_field = 'id'


class ApproveSuggestionView(APIView):
    """
    POST /api/v1/promotions/suggestions/<uuid:id>/approve/

    One-click promotion creation from a suggestion.
    Creates a Promotion and PromotionItem using the suggested discount config,
    then marks the suggestion as 'approved'.

    Optional request body to override defaults:
    {
        "title": "Custom title (optional)",
        "ends_days": 7,             // override suggested duration
        "discount_value": 15.00     // override suggested discount
    }
    """
    permission_classes = [IsManager]

    def post(self, request, id):
        try:
            suggestion = DiscountSuggestion.objects.select_related(
                'variant__product'
            ).get(id=id, status='pending')
        except DiscountSuggestion.DoesNotExist:
            return Response(
                {'error': 'Suggestion not found or already actioned.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        manager_id = uuid.UUID(request.user.username)
        now = timezone.now()

        # Allow manager to override title and duration
        title = request.data.get(
            'title',
            f"{suggestion.variant.product.name} — {suggestion.priority.title()} Discount Offer"
        )
        ends_days = int(request.data.get('ends_days', suggestion.suggested_ends_days))
        discount_value = request.data.get('discount_value', suggestion.suggested_discount_value)

        from django.db import transaction
        with transaction.atomic():
            promotion = Promotion.objects.create(
                title=title,
                description=suggestion.reason_summary,
                discount_type=suggestion.suggested_discount_type,
                discount_value=discount_value,
                starts_at=now,
                ends_at=now + timedelta(days=ends_days),
                is_active=True,
                created_by=manager_id,
            )
            PromotionItem.objects.create(
                promotion=promotion,
                variant_id=suggestion.variant_id,
            )

            suggestion.status = 'approved'
            suggestion.approved_promotion = promotion
            suggestion.save(update_fields=['status', 'approved_promotion_id'])

        return Response({
            'suggestion_id': str(suggestion.id),
            'promotion_id': str(promotion.id),
            'title': promotion.title,
            'discount_type': promotion.discount_type,
            'discount_value': str(promotion.discount_value),
            'starts_at': promotion.starts_at.isoformat(),
            'ends_at': promotion.ends_at.isoformat(),
            'message': 'Promotion is now live.',
        }, status=status.HTTP_201_CREATED)


class DismissSuggestionView(APIView):
    """
    POST /api/v1/promotions/suggestions/<uuid:id>/dismiss/
    Dismisses a suggestion — it will not resurface for 30 days.
    """
    permission_classes = [IsManager]

    def post(self, request, id):
        try:
            suggestion = DiscountSuggestion.objects.get(id=id, status='pending')
        except DiscountSuggestion.DoesNotExist:
            return Response(
                {'error': 'Suggestion not found or already actioned.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        snooze_days = int(request.data.get('snooze_days', 30))
        suggestion.status = 'dismissed'
        suggestion.dismissed_until = timezone.now() + timedelta(days=snooze_days)
        suggestion.save(update_fields=['status', 'dismissed_until'])

        return Response({'message': f'Suggestion dismissed for {snooze_days} days.'})
```

---

### Cloud Tasks Worker — `tasks/views.py` extension

Add a new view to the existing `tasks/` app:

```python
class RunDiscountAnalysisView(APIView):
    """
    POST /api/v1/tasks/run-discount-analysis/

    Internal Cloud Tasks endpoint — triggered by Cloud Scheduler nightly.
    Not accessible from the internet (Cloud Tasks adds a verified task header).
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        task_name = request.headers.get('X-CloudTasks-TaskName', '')
        if not task_name and not settings.DEBUG:
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        from promotions.analytics_service import run_discount_analysis
        result = run_discount_analysis()

        return Response({
            'status': 'ok',
            'new_suggestions': result['new'],
            'updated_suggestions': result['updated'],
            'expired_suggestions': result['expired'],
        })
```

---

### Cloud Scheduler Configuration

Create a Cloud Scheduler job to fire nightly at 2:00 AM IST (20:30 UTC):

```yaml
# gcloud scheduler jobs create http dwarikas-discount-analysis \
#   --schedule="30 20 * * *" \
#   --uri="https://<CLOUD_RUN_URL>/api/v1/tasks/run-discount-analysis/" \
#   --http-method=POST \
#   --oidc-service-account-email=<SERVICE_ACCOUNT> \
#   --time-zone="UTC"

name: dwarikas-discount-analysis
schedule: "30 20 * * *"
timeZone: UTC
httpTarget:
  uri: https://<CLOUD_RUN_URL>/api/v1/tasks/run-discount-analysis/
  httpMethod: POST
  oidcToken:
    serviceAccountEmail: cloudrun-serviceaccount@<PROJECT_ID>.iam.gserviceaccount.com
```

The job can also be **triggered on-demand** by a manager via:

```
POST /api/v1/tasks/run-discount-analysis/    (DEBUG mode or Cloud Tasks token)
```

---

## Supabase Migration

```sql
CREATE TABLE IF NOT EXISTS public.discount_suggestions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id                  UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE CASCADE,
    product_id                  UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    discount_score              INTEGER NOT NULL,
    priority                    TEXT NOT NULL CHECK (priority IN ('critical', 'high', 'medium')),
    reason_summary              TEXT NOT NULL,
    reasons                     JSONB NOT NULL DEFAULT '{}',
    suggested_discount_type     TEXT NOT NULL CHECK (suggested_discount_type IN ('percentage', 'flat_amount')),
    suggested_discount_value    DECIMAL(10, 2) NOT NULL,
    suggested_ends_days         INTEGER NOT NULL DEFAULT 14,
    current_stock               INTEGER NOT NULL,
    avg_monthly_sales           DECIMAL(10, 2) NOT NULL DEFAULT 0,
    days_since_last_order       INTEGER,
    cost_price                  DECIMAL(12, 2),
    margin_pct                  DECIMAL(5, 2),
    status                      TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'dismissed', 'expired')),
    dismissed_until             TIMESTAMPTZ,
    approved_promotion_id       UUID REFERENCES public.promotions(id) ON DELETE SET NULL,
    analysed_at                 TIMESTAMPTZ NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One active suggestion per variant
CREATE UNIQUE INDEX IF NOT EXISTS idx_discount_suggestion_active_variant
    ON public.discount_suggestions (variant_id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_discount_suggestions_status_score
    ON public.discount_suggestions (status, discount_score DESC);
```

---

## API Response Examples

### `GET /api/v1/promotions/suggestions/` — Manager Dashboard Feed

```json
{
  "count": 3,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "variant": {
        "id": "uuid",
        "sku": "DW-RICE-1KG",
        "retail_price": "120.00",
        "stock_quantity": 245,
        "product": { "name": "Basmati Rice", "category": "Rice & Grains" }
      },
      "discount_score": 87,
      "priority": "critical",
      "reason_summary": "High stock: 245 units (8.2× monthly sales) · No order in 52 days",
      "reasons": {
        "stock_score": 82,
        "recency_score": 95,
        "abandonment_score": 40,
        "margin_score": 75
      },
      "suggested_discount_type": "percentage",
      "suggested_discount_value": "18.00",
      "suggested_ends_days": 7,
      "current_stock": 245,
      "avg_monthly_sales": "29.80",
      "days_since_last_order": 52,
      "margin_pct": "29.17",
      "status": "pending",
      "analysed_at": "2026-06-07T20:30:00Z"
    }
  ]
}
```

### `POST /api/v1/promotions/suggestions/<id>/approve/` — One-click Activate

**Request:** (empty body uses defaults; or override below)
```json
{
  "title": "Basmati Rice Clearance — 18% Off",
  "ends_days": 7
}
```

**Response (201):**
```json
{
  "suggestion_id": "uuid",
  "promotion_id": "uuid",
  "title": "Basmati Rice Clearance — 18% Off",
  "discount_type": "percentage",
  "discount_value": "18.00",
  "starts_at": "2026-06-07T22:15:00Z",
  "ends_at": "2026-06-14T22:15:00Z",
  "message": "Promotion is now live."
}
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `SUGGESTION_THRESHOLD_SCORE` | Override the minimum score to surface a suggestion (default: 60). Optional. |
| `MIN_MARGIN_PCT_TO_SUGGEST` | Override the minimum gross margin % required (default: 15). Optional. |

Both default to hardcoded constants if not set. No additional external services needed.

---

## Acceptance Criteria

- [ ] `run_discount_analysis()` correctly scores all eligible variants using composite signals
- [ ] Variants with `stock_quantity < MIN_STOCK_TO_ANALYSE` are skipped
- [ ] Variants with margin below `MIN_MARGIN_PCT_TO_SUGGEST` are not surfaced
- [ ] `DiscountSuggestion` records are upserted — existing `pending` suggestions are updated in place
- [ ] Previously dismissed suggestions are not re-surfaced until `dismissed_until` date passes
- [ ] Suggestions for variants that no longer score above threshold are transitioned to `expired`
- [ ] `GET /api/v1/promotions/suggestions/` returns pending suggestions ordered by score desc, filterable by `?priority=`
- [ ] `POST /api/v1/promotions/suggestions/<id>/approve/` creates a live `Promotion` + `PromotionItem` atomically and marks suggestion as `approved`
- [ ] `POST /api/v1/promotions/suggestions/<id>/dismiss/` marks suggestion as `dismissed` with a 30-day snooze
- [ ] The `approve/` endpoint accepts optional `title`, `ends_days`, `discount_value` overrides
- [ ] Cloud Tasks worker endpoint (`POST /tasks/run-discount-analysis/`) is protected by task header check
- [ ] Unit tests cover: scoring logic, edge cases (no cost data, no orders, zero sales), approve/dismiss flows
- [ ] Integration test: end-to-end — seed variant + order data → trigger analysis → check suggestion → approve → check live promotion

---

## Django App Layout Changes

```
promotions/
├── __init__.py
├── apps.py
├── models.py                       # + DiscountSuggestion model
├── serializers.py                  # + DiscountSuggestionSerializer
├── services.py
├── analytics_service.py            # NEW — scoring engine
├── whatsapp_service.py
├── views.py
├── suggestion_views.py             # NEW — suggestion list/approve/dismiss views
├── urls.py                         # + 4 new suggestion routes
└── tests/
    ├── __init__.py
    ├── test_promotions.py
    ├── test_analytics_service.py   # NEW
    ├── test_suggestions.py         # NEW
    ├── test_checkout_integration.py
    └── test_whatsapp_broadcast.py

tasks/
└── views.py                        # + RunDiscountAnalysisView
```
