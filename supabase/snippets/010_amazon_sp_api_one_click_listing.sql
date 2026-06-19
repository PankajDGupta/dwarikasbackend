-- DDL snippet for Amazon SP-API One-Click Product Listing (Spec 23)

-- Create custom enum types safely
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'amazon_region_enum') THEN
        CREATE TYPE amazon_region_enum AS ENUM ('NA', 'EU', 'FE');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'listing_sync_status_enum') THEN
        CREATE TYPE listing_sync_status_enum AS ENUM (
            'PENDING', 'SUBMITTED', 'ACTIVE', 'INVALID', 'ERROR', 'SUPPRESSED'
        );
    END IF;
END$$;

-- Create credentials table
CREATE TABLE IF NOT EXISTS public.amazon_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id VARCHAR(255) NOT NULL UNIQUE,
    lwa_client_id VARCHAR(255) NOT NULL,
    lwa_client_secret TEXT NOT NULL,
    lwa_refresh_token TEXT NOT NULL,
    region amazon_region_enum NOT NULL DEFAULT 'EU',
    primary_marketplace_id VARCHAR(50) NOT NULL,
    authorized_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Create listings sync table
CREATE TABLE IF NOT EXISTS public.amazon_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    sku VARCHAR(100) NOT NULL UNIQUE,
    asin VARCHAR(10) NULL,
    marketplace_id VARCHAR(50) NOT NULL,
    sync_status listing_sync_status_enum NOT NULL DEFAULT 'PENDING',
    submission_id UUID NULL,
    validation_issues JSONB DEFAULT '[]'::jsonb,
    price_synced NUMERIC(10, 2) NULL,
    quantity_synced INTEGER DEFAULT 0,
    last_synced_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_amazon_listings_sku ON public.amazon_listings(sku);
CREATE INDEX IF NOT EXISTS idx_amazon_listings_product ON public.amazon_listings(product_id);

-- Enable RLS
ALTER TABLE public.amazon_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.amazon_listings ENABLE ROW LEVEL SECURITY;

-- Allow only platform Admins to read/modify credentials
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'amazon_credentials' AND policyname = 'admin_full_access'
    ) THEN
        CREATE POLICY admin_full_access ON public.amazon_credentials
            FOR ALL USING (auth.jwt() ->> 'role' = 'admin');
    END IF;
END$$;

-- Staff and Admin can manage listings tracking
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'amazon_listings' AND policyname = 'staff_admin_listing_access'
    ) THEN
        CREATE POLICY staff_admin_listing_access ON public.amazon_listings
            FOR ALL USING (auth.jwt() ->> 'role' IN ('admin', 'staff'));
    END IF;
END$$;
