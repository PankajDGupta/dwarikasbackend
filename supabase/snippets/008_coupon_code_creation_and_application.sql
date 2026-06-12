-- DDL snippet for Coupon Code Creation & Application (Spec 20)

-- Create coupons table
CREATE TABLE IF NOT EXISTS public.coupons (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                TEXT UNIQUE NOT NULL,   -- Always stored UPPERCASE
    description         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      DECIMAL(10, 2) NOT NULL,
    max_discount_cap    DECIMAL(10, 2),
    min_order_value     DECIMAL(10, 2),
    max_uses            INTEGER,                -- NULL = unlimited
    uses_per_user       INTEGER NOT NULL DEFAULT 1,
    specific_user_id    UUID,                   -- NULL = public coupon
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_until         TIMESTAMPTZ,            -- NULL = no expiry
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source              TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'gaming_reward', 'referral'))
);

-- Create coupon redemptions audit log
CREATE TABLE IF NOT EXISTS public.coupon_redemptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coupon_id           UUID NOT NULL REFERENCES public.coupons(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL,
    order_id            UUID REFERENCES public.orders(id) ON DELETE SET NULL,
    reservation_id      UUID REFERENCES public.reservations(id) ON DELETE SET NULL,
    discount_applied    DECIMAL(12, 2) NOT NULL,
    redeemed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extend reservations table with coupon columns
ALTER TABLE public.reservations
    ADD COLUMN IF NOT EXISTS coupon_id       UUID REFERENCES public.coupons(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS coupon_discount DECIMAL(12, 2),
    ADD COLUMN IF NOT EXISTS final_price     DECIMAL(12, 2);

-- Create performance indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_coupons_code ON public.coupons (UPPER(code));
CREATE INDEX IF NOT EXISTS idx_coupons_specific_user ON public.coupons (specific_user_id) WHERE specific_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_user ON public.coupon_redemptions (user_id);
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_coupon ON public.coupon_redemptions (coupon_id);
