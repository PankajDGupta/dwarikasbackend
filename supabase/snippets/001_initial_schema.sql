-- =============================================================================
-- 001_initial_schema.sql
-- Dwarikas Backend — Initial Supabase Production Schema
--
-- Extracted from context/system_design.md (Section 3. Database Architecture)
-- Apply this FIRST in the Supabase SQL Editor before any other snippet.
--
-- Tables covered:
--   public.profiles            — User metadata & RBAC roles
--   public.products            — Master product catalog
--   public.product_variants    — SKU-level variant with stock
--   public.reservations        — Active checkout holds (10-min locks)
--   public.orders              — Completed order transaction logs
--
-- All subsequent schema changes are in numbered snippets 002 onward.
-- DO NOT run this if the tables already exist (use IF NOT EXISTS guard below).
-- =============================================================================

-- Profiles table for user metadata and Role-Based Access Control (RBAC)
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('customer', 'staff', 'manager')),
    phone_number TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Master products table storing catalog metadata
CREATE TABLE IF NOT EXISTS public.products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    hsn_code TEXT NOT NULL,
    gst_slab NUMERIC(5, 2) NOT NULL DEFAULT 18.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Variant configuration with physical stock mapping
CREATE TABLE IF NOT EXISTS public.product_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    sku TEXT UNIQUE NOT NULL,
    barcode TEXT UNIQUE,
    size TEXT,
    color TEXT,
    stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
    retail_price NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Promotions table (must exist before reservations — reservations FK refs it)
CREATE TABLE IF NOT EXISTS public.promotions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    description         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      NUMERIC(10, 2) NOT NULL,
    max_discount_cap    NUMERIC(10, 2),
    min_order_value     NUMERIC(10, 2),
    banner_image_url    TEXT,
    starts_at           TIMESTAMPTZ NOT NULL,
    ends_at             TIMESTAMPTZ,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promotions_active_dates ON public.promotions (is_active, starts_at, ends_at);

-- Active checkout inventory holds
CREATE TABLE IF NOT EXISTS public.reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    reserved_quantity INTEGER NOT NULL CHECK (reserved_quantity > 0),
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'expired')) DEFAULT 'active',
    effective_price NUMERIC(12, 2),
    promotion_id UUID REFERENCES public.promotions(id) ON DELETE SET NULL
);

-- Complete order transaction logs
-- payment_method: 'online' added for Razorpay-managed flows (Spec #17)
-- payment_status: 'refunded' added for Razorpay refund flows (Spec #17)
-- carrier_status, tracking_reference: added for External Partner gateway (Spec #15)
CREATE TABLE IF NOT EXISTS public.orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    total_amount NUMERIC(12, 2) NOT NULL,
    gst_amount NUMERIC(12, 2) NOT NULL,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('UPI', 'card', 'cash', 'online')),
    payment_status TEXT NOT NULL CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded')),
    carrier_status TEXT CHECK (carrier_status IN ('staged', 'picked_up', 'in_transit', 'delivered')),
    tracking_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Row Level Security
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_variants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.promotions ENABLE ROW LEVEL SECURITY;

-- RLS Policies: profiles — users manage their own row; staff/managers see all
CREATE POLICY profiles_self_select ON public.profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY profiles_staff_select ON public.profiles
    FOR SELECT USING (auth.jwt() ->> 'role' IN ('staff', 'manager'));

-- RLS Policies: products — public read, staff/manager write
CREATE POLICY products_public_read ON public.products
    FOR SELECT USING (true);

CREATE POLICY products_staff_write ON public.products
    FOR ALL USING (auth.jwt() ->> 'role' IN ('staff', 'manager'));

-- RLS Policies: product_variants — public read, staff/manager write
CREATE POLICY variants_public_read ON public.product_variants
    FOR SELECT USING (true);

CREATE POLICY variants_staff_write ON public.product_variants
    FOR ALL USING (auth.jwt() ->> 'role' IN ('staff', 'manager'));

-- RLS Policies: reservations — owner-isolated; staff/managers see all
CREATE POLICY reservations_owner ON public.reservations
    FOR ALL USING (auth.uid() = user_id);

CREATE POLICY reservations_staff ON public.reservations
    FOR ALL USING (auth.jwt() ->> 'role' IN ('staff', 'manager'));

-- RLS Policies: orders — owner-isolated; staff/managers see all
CREATE POLICY orders_owner ON public.orders
    FOR ALL USING (auth.uid() = user_id);

CREATE POLICY orders_staff ON public.orders
    FOR ALL USING (auth.jwt() ->> 'role' IN ('staff', 'manager'));

-- RLS Policies: promotions — public read, manager write
CREATE POLICY promotions_public_read ON public.promotions
    FOR SELECT USING (true);

CREATE POLICY promotions_manager_write ON public.promotions
    FOR ALL USING (auth.jwt() ->> 'role' = 'manager');

-- Security definer helper: avoids recursive policy evaluation on profiles table
CREATE OR REPLACE FUNCTION public.check_user_is_staff(user_uuid UUID)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = user_uuid
          AND role IN ('staff', 'manager')
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE OR REPLACE FUNCTION public.check_user_is_manager(user_uuid UUID)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = user_uuid
          AND role = 'manager'
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
