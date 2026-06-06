-- =============================================================================
-- Supabase Migration: 005 — External Partner API Gateway
-- Spec: context/feature-spec/spec15-External_Partner_API_Gateway.md
-- Date: 2026-06-06
-- =============================================================================

-- 1. Create check_user_is_manager helper function
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

-- 2. Create external_api_keys table
CREATE TABLE IF NOT EXISTS public.external_api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,     -- SHA-256 hash of the raw API key
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Enable Row Level Security (RLS) on external_api_keys
ALTER TABLE public.external_api_keys ENABLE ROW LEVEL SECURITY;

-- 4. Create RLS Policy for managers only
CREATE POLICY manager_all_external_api_keys ON public.external_api_keys
    FOR ALL
    TO authenticated
    USING (public.check_user_is_manager(auth.uid()))
    WITH CHECK (public.check_user_is_manager(auth.uid()));

-- 5. Extend public.orders table with shipment tracking columns
ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS carrier_status TEXT CHECK (carrier_status IN ('staged', 'picked_up', 'in_transit', 'delivered')),
    ADD COLUMN IF NOT EXISTS tracking_reference TEXT;
