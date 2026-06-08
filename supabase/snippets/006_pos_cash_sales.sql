-- =============================================================================
-- Supabase Migration: 006 — POS Cash Sales & In-Store Bill Generation
-- Spec: context/feature-spec/spec18-POS_Cash_Sales_and_Bill_Generation.md
-- Date: 2026-06-08
-- =============================================================================

-- 1. Create public.pos_carts table
CREATE TABLE IF NOT EXISTS public.pos_carts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    staff_user_id   UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    customer_phone  TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'confirmed', 'abandoned')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pos_cart_staff ON public.pos_carts(staff_user_id);

-- 2. Create public.pos_cart_items table
CREATE TABLE IF NOT EXISTS public.pos_cart_items (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id     UUID NOT NULL REFERENCES public.pos_carts(id) ON DELETE CASCADE,
    variant_id  UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(12, 2) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (cart_id, variant_id)
);

-- 3. Create public.order_items table
CREATE TABLE IF NOT EXISTS public.order_items (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id                UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
    variant_id              UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    sku_snapshot            TEXT NOT NULL,
    product_name_snapshot   TEXT NOT NULL,
    hsn_code_snapshot       TEXT NOT NULL,
    gst_slab_snapshot       NUMERIC(5, 2) NOT NULL,
    quantity                INTEGER NOT NULL CHECK (quantity > 0),
    unit_price              NUMERIC(12, 2) NOT NULL,
    subtotal                NUMERIC(12, 2) NOT NULL,
    gst_amount              NUMERIC(12, 2) NOT NULL,
    line_total              NUMERIC(12, 2) NOT NULL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON public.order_items(order_id);

-- 4. Enable Row Level Security (RLS) on all three tables
ALTER TABLE public.pos_carts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pos_cart_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_items ENABLE ROW LEVEL SECURITY;

-- 5. Create RLS Policies for staff & managers (since only staff/managers operate POS)
CREATE POLICY staff_manager_all_pos_carts ON public.pos_carts
    FOR ALL
    TO authenticated
    USING (public.check_user_is_staff(auth.uid()))
    WITH CHECK (public.check_user_is_staff(auth.uid()));

CREATE POLICY staff_manager_all_pos_cart_items ON public.pos_cart_items
    FOR ALL
    TO authenticated
    USING (public.check_user_is_staff(auth.uid()))
    WITH CHECK (public.check_user_is_staff(auth.uid()));

CREATE POLICY staff_manager_all_order_items ON public.order_items
    FOR ALL
    TO authenticated
    USING (public.check_user_is_staff(auth.uid()))
    WITH CHECK (public.check_user_is_staff(auth.uid()));
