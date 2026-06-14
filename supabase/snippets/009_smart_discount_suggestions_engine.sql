-- DDL snippet for Smart Discount Suggestions Engine (Spec 22)

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

-- Index ensuring at most one active (pending) suggestion per variant
CREATE UNIQUE INDEX IF NOT EXISTS idx_discount_suggestion_active_variant
    ON public.discount_suggestions (variant_id)
    WHERE status = 'pending';

-- Index for dashboard feeds sorted by score descending
CREATE INDEX IF NOT EXISTS idx_discount_suggestions_status_score
    ON public.discount_suggestions (status, discount_score DESC);
