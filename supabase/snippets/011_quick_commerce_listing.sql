-- DDL snippet for Quick-Commerce Platform Listings (Spec 24)

-- Create custom enum types safely
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'qc_platform_enum') THEN
        CREATE TYPE qc_platform_enum AS ENUM ('blinkit', 'jiomart');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'qc_listing_status_enum') THEN
        CREATE TYPE qc_listing_status_enum AS ENUM (
            'DRAFT',
            'VALIDATING',
            'SUBMITTED',
            'PENDING_REVIEW',
            'ACTIVE',
            'INACTIVE',
            'REJECTED',
            'ERROR'
        );
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'qc_po_status_enum') THEN
        CREATE TYPE qc_po_status_enum AS ENUM (
            'RECEIVED',
            'VERIFIED',
            'DISPATCHED',
            'ASN_SENT',
            'INWARDED',
            'CANCELLED'
        );
    END IF;
END$$;

-- Create platform listings sync table
CREATE TABLE IF NOT EXISTS public.qc_platform_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    variant_id UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    platform qc_platform_enum NOT NULL,
    platform_sku TEXT,
    platform_upc TEXT,
    asin_equivalent TEXT,
    sync_status qc_listing_status_enum NOT NULL DEFAULT 'DRAFT',
    trace_id TEXT,
    submission_guid UUID,
    validation_issues JSONB DEFAULT '[]'::jsonb,
    mrp_snapshot NUMERIC(10, 2),
    selling_price_snapshot NUMERIC(10, 2),
    fssai_license TEXT,
    barcode_validated BOOLEAN DEFAULT FALSE,
    image_validated BOOLEAN DEFAULT FALSE,
    last_synced_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qc_listings_product ON public.qc_platform_listings(product_id);
CREATE INDEX IF NOT EXISTS idx_qc_listings_platform_sku ON public.qc_platform_listings(platform, platform_sku);
CREATE INDEX IF NOT EXISTS idx_qc_listings_status ON public.qc_platform_listings(sync_status);

-- Create platform credentials table
CREATE TABLE IF NOT EXISTS public.qc_platform_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform qc_platform_enum NOT NULL UNIQUE,
    fynd_username TEXT,
    fynd_access_token TEXT,
    fynd_token_expires_at TIMESTAMP WITH TIME ZONE,
    blinkit_vendor_id TEXT,
    blinkit_receiver_code TEXT,
    blinkit_webhook_secret TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Create warehouse and location mappings table
CREATE TABLE IF NOT EXISTS public.qc_warehouse_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform qc_platform_enum NOT NULL,
    internal_facility_code TEXT NOT NULL,
    platform_location_id TEXT NOT NULL,
    mapping_type TEXT NOT NULL CHECK (mapping_type IN ('facility', 'pincode')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_qc_wh_platform_location
    ON public.qc_warehouse_mappings(platform, platform_location_id);

-- Create purchase orders table (Blinkit B2B POs)
CREATE TABLE IF NOT EXISTS public.qc_purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform qc_platform_enum NOT NULL,
    platform_po_id TEXT NOT NULL UNIQUE,
    vendor_id TEXT,
    facility_code TEXT,
    po_status qc_po_status_enum NOT NULL DEFAULT 'RECEIVED',
    total_amount NUMERIC(12, 2),
    asn_reference TEXT,
    raw_payload JSONB,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    dispatched_at TIMESTAMP WITH TIME ZONE,
    inwarded_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Create purchase order line items table
CREATE TABLE IF NOT EXISTS public.qc_po_line_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id UUID NOT NULL REFERENCES public.qc_purchase_orders(id) ON DELETE CASCADE,
    variant_id UUID REFERENCES public.product_variants(id) ON DELETE SET NULL,
    platform_sku TEXT NOT NULL,
    ordered_quantity INTEGER NOT NULL CHECK (ordered_quantity > 0),
    delivered_quantity INTEGER DEFAULT 0 CHECK (delivered_quantity >= 0),
    unit_price NUMERIC(10, 2),
    mrp NUMERIC(10, 2)
);

-- Enable RLS
ALTER TABLE public.qc_platform_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.qc_platform_credentials ENABLE ROW LEVEL SECURITY;

-- Staff and Admin can manage listings
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'qc_platform_listings' AND policyname = 'qc_staff_admin_access'
    ) THEN
        CREATE POLICY qc_staff_admin_access ON public.qc_platform_listings
            FOR ALL USING (auth.jwt() ->> 'role' IN ('admin', 'staff', 'manager'));
    END IF;
END$$;

-- Only platform managers/admins can read/modify credentials
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'qc_platform_credentials' AND policyname = 'qc_manager_only'
    ) THEN
        CREATE POLICY qc_manager_only ON public.qc_platform_credentials
            FOR ALL USING (auth.jwt() ->> 'role' IN ('admin', 'manager'));
    END IF;
END$$;
