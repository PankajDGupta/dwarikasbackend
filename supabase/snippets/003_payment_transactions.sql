-- Spec #17 — Payment Gateway Integration (Razorpay)
-- Apply this via: supabase db query "<statement>" (one statement at a time per project convention)
-- Or via the Supabase Dashboard SQL editor.
--
-- Run each statement individually:
-- 1. Create the payment_transactions table
-- 2. Create index on razorpay_order_id for webhook lookups
-- 3. Create index on user_id for payment history queries
-- 4. Patch orders table: add 'online' payment method
-- 5. Patch orders table: add 'refunded' payment status

-- ── 1. payment_transactions table ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.payment_transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reservation_id      UUID REFERENCES public.reservations(id) ON DELETE SET NULL,
    order_id            UUID UNIQUE REFERENCES public.orders(id) ON DELETE SET NULL,
    user_id             UUID NOT NULL,
    razorpay_order_id   TEXT UNIQUE NOT NULL,
    razorpay_payment_id TEXT,
    razorpay_signature  TEXT,
    amount_paise        INTEGER NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'INR',
    status              TEXT NOT NULL DEFAULT 'created'
                        CHECK (status IN ('created', 'attempted', 'paid', 'failed', 'refunded')),
    failure_reason      TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2. Index for webhook lookups (razorpay_order_id) ──────────────────────────

CREATE INDEX IF NOT EXISTS idx_payment_txn_rp_order
    ON public.payment_transactions(razorpay_order_id);

-- ── 3. Index for user-level payment history ────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_payment_txn_user
    ON public.payment_transactions(user_id);

-- ── 4. Patch orders.payment_method — add 'online' choice ──────────────────────
-- Razorpay-backed UPI/card payments use payment_method='online'

ALTER TABLE public.orders DROP CONSTRAINT IF EXISTS orders_payment_method_check;
ALTER TABLE public.orders ADD CONSTRAINT orders_payment_method_check
    CHECK (payment_method IN ('UPI', 'card', 'cash', 'online'));

-- ── 5. Patch orders.payment_status — add 'refunded' choice ────────────────────

ALTER TABLE public.orders DROP CONSTRAINT IF EXISTS orders_payment_status_check;
ALTER TABLE public.orders ADD CONSTRAINT orders_payment_status_check
    CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded'));
