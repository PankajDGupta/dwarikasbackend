-- promotions table
CREATE TABLE IF NOT EXISTS public.promotions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    description         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      DECIMAL(10, 2) NOT NULL,
    max_discount_cap    DECIMAL(10, 2),
    min_order_value     DECIMAL(10, 2),
    banner_image_url    TEXT,
    starts_at           TIMESTAMPTZ NOT NULL,
    ends_at             TIMESTAMPTZ,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- promotion_items table
CREATE TABLE IF NOT EXISTS public.promotion_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promotion_id    UUID NOT NULL REFERENCES public.promotions(id) ON DELETE CASCADE,
    product_id      UUID REFERENCES public.products(id) ON DELETE CASCADE,
    variant_id      UUID REFERENCES public.product_variants(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_product_or_variant CHECK (
        product_id IS NOT NULL OR variant_id IS NOT NULL
    )
);

-- Extend reservations table
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS effective_price DECIMAL(12, 2),
    ADD COLUMN IF NOT EXISTS promotion_id UUID REFERENCES public.promotions(id) ON DELETE SET NULL;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_promotions_active_dates
    ON public.promotions (is_active, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_promotion_items_product
    ON public.promotion_items (product_id);
CREATE INDEX IF NOT EXISTS idx_promotion_items_variant
    ON public.promotion_items (variant_id);

-- promotion_broadcasts table
CREATE TABLE IF NOT EXISTS public.promotion_broadcasts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promotion_id    UUID NOT NULL REFERENCES public.promotions(id) ON DELETE CASCADE,
    phone_number    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
    failure_reason  TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_by         UUID NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_promotion_broadcasts_promotion
    ON public.promotion_broadcasts (promotion_id);
