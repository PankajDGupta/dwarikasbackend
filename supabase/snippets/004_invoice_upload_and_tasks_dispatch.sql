-- =============================================================================
-- Supabase Migration: 004 — Invoice Upload & Tasks Dispatch
-- Spec: context/feature-spec/spec10-Invoice_Upload_and_Cloud_Tasks_Dispatch.md
-- Date: 2026-06-06
-- =============================================================================

-- 1. Create purchase_invoices table
CREATE TABLE IF NOT EXISTS public.purchase_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number TEXT UNIQUE,
    vendor_name TEXT,
    vendor_gstin TEXT,
    issued_at DATE,
    gcs_object_path TEXT NOT NULL,          -- gs://bucket-name/path/to/file
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'review', 'confirmed', 'failed')),
    uploaded_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Create invoice_line_items table
CREATE TABLE IF NOT EXISTS public.invoice_line_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES public.purchase_invoices(id) ON DELETE CASCADE,
    sku TEXT,
    description TEXT,
    quantity INTEGER,
    unit_price NUMERIC(12, 2),
    gst_rate NUMERIC(5, 2),
    confidence_score NUMERIC(4, 3),          -- 0.000–1.000 from Document AI
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Enable Row Level Security (RLS) on both tables
ALTER TABLE public.purchase_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invoice_line_items ENABLE ROW LEVEL SECURITY;

-- 4. Create RLS Policy for staff & managers on purchase_invoices
CREATE POLICY staff_manager_all_purchase_invoices ON public.purchase_invoices
    FOR ALL
    TO authenticated
    USING (public.check_user_is_staff(auth.uid()))
    WITH CHECK (public.check_user_is_staff(auth.uid()));

-- 5. Create RLS Policy for staff & managers on invoice_line_items
CREATE POLICY staff_manager_all_invoice_line_items ON public.invoice_line_items
    FOR ALL
    TO authenticated
    USING (public.check_user_is_staff(auth.uid()))
    WITH CHECK (public.check_user_is_staff(auth.uid()));
