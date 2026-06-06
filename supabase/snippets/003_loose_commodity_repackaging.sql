-- =============================================================================
-- Supabase Migration: 003 — Loose Commodity Repackaging & Packet Barcodes
-- Spec: context/feature-spec/spec09b-Loose_Product_Repackaging.md
-- Date: 2026-06-06
-- =============================================================================

-- 1. Mark which products are bulk/loose commodities
ALTER TABLE public.products
  ADD COLUMN IF NOT EXISTS is_loose_commodity BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Net weight or volume per packet (e.g. 1.000 for a 1kg pack)
ALTER TABLE public.product_variants
  ADD COLUMN IF NOT EXISTS net_weight_value NUMERIC(10, 3) NULL;

-- 3. Unit of measure default and constraint (merged with legacy UOMs)
ALTER TABLE public.product_variants
  ALTER COLUMN unit_of_measure SET DEFAULT 'unit';

UPDATE public.product_variants
  SET unit_of_measure = 'unit'
  WHERE unit_of_measure IS NULL;

ALTER TABLE public.product_variants
  ALTER COLUMN unit_of_measure SET NOT NULL;

ALTER TABLE public.product_variants
  DROP CONSTRAINT IF EXISTS chk_unit_of_measure;

ALTER TABLE public.product_variants
  ADD CONSTRAINT chk_unit_of_measure
  CHECK (unit_of_measure IN ('unit', 'kg', 'g', 'litre', 'ml', 'L', 'pcs', 'pack'));

-- 4. Audit log: one row per packaging machine run
CREATE TABLE IF NOT EXISTS public.packaging_jobs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_description  TEXT NOT NULL,
    source_variant_id   UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    bulk_quantity_used  NUMERIC(10, 3),
    bulk_unit           TEXT,
    notes               TEXT,
    created_by          UUID NOT NULL REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. One row per packet size created in the job
CREATE TABLE IF NOT EXISTS public.packaging_job_outputs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID NOT NULL REFERENCES public.packaging_jobs(id) ON DELETE CASCADE,
    variant_id          UUID NOT NULL REFERENCES public.product_variants(id) ON DELETE RESTRICT,
    packets_produced    INTEGER NOT NULL CHECK (packets_produced > 0),
    weight_per_packet   NUMERIC(10, 3),
    unit_of_measure     TEXT,
    barcode_value       TEXT,
    is_new_variant      BOOLEAN NOT NULL DEFAULT FALSE
);

-- 6. Index for fast job output lookups
CREATE INDEX IF NOT EXISTS idx_packaging_job_outputs_job_id
  ON public.packaging_job_outputs(job_id);
