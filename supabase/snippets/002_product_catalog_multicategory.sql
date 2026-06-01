-- =============================================================================
-- Supabase Migration: 002 — Multi-Category Product Catalog Extension
-- Spec: context/feature-spec/spec06-Product_Catalog_API.md (amended)
-- Date: 2026-06-01
-- =============================================================================
-- Extends the public.products table to support multiple product categories
-- (grocery, apparel, general merchandise). New columns are all nullable/defaulted
-- so existing grocery data is fully backward-compatible.
-- =============================================================================

-- 1. Add product_type discriminator column
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS product_type TEXT NOT NULL DEFAULT 'general'
        CHECK (product_type IN ('grocery', 'apparel', 'general'));

COMMENT ON COLUMN public.products.product_type IS
    'Product category discriminator. Drives which optional fields apply.
     grocery → use dietary_type
     apparel → use material, gender_target, fit_type
     general → all optional fields are inapplicable';

-- 2. Backfill existing rows: all existing products are grocery items
UPDATE public.products
SET product_type = 'grocery'
WHERE product_type = 'general';

-- 3. Add apparel-specific columns
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS material TEXT,
    ADD COLUMN IF NOT EXISTS gender_target TEXT DEFAULT 'none'
        CHECK (gender_target IN ('men', 'women', 'unisex', 'boys', 'girls', 'none')),
    ADD COLUMN IF NOT EXISTS fit_type TEXT;

COMMENT ON COLUMN public.products.material IS
    'Fabric or material composition (e.g., ''100% Cotton'', ''Polyester Blend'').
     Only populate for apparel product_type.';

COMMENT ON COLUMN public.products.gender_target IS
    'Target gender for apparel items.
     Values: men | women | unisex | boys | girls | none (default, for non-apparel).';

COMMENT ON COLUMN public.products.fit_type IS
    'Garment fit descriptor (e.g., ''Slim Fit'', ''Regular Fit'', ''Oversized'').
     Only populate for apparel product_type.';

-- 4. Add index on product_type for efficient catalog browsing by category
CREATE INDEX IF NOT EXISTS idx_products_product_type
    ON public.products (product_type);

-- 5. Add index on gender_target for apparel-specific queries
CREATE INDEX IF NOT EXISTS idx_products_gender_target
    ON public.products (gender_target);
