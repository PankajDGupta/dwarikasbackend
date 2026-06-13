-- Reward tiers (manager-configured prize table)
CREATE TABLE IF NOT EXISTS public.reward_tiers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    game_type           TEXT NOT NULL,
    win_level           TEXT NOT NULL,         -- 'any' acts as wildcard
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('percentage', 'flat_amount')),
    discount_value      DECIMAL(10, 2) NOT NULL,
    max_discount_cap    DECIMAL(10, 2),
    valid_days          INTEGER NOT NULL DEFAULT 30,
    description         TEXT,
    notify_whatsapp     BOOLEAN NOT NULL DEFAULT TRUE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Game plays (one row per play consumed — tracks quota)
CREATE TABLE IF NOT EXISTS public.game_plays (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    game_session_id     TEXT UNIQUE NOT NULL,   -- Unity GUID — deduplication key
    game_type           TEXT NOT NULL,
    play_source         TEXT NOT NULL DEFAULT 'order' CHECK (play_source IN ('order', 'rewarded_ad')),
    played_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Game rewards (win events + issued coupons — audit log)
CREATE TABLE IF NOT EXISTS public.game_rewards (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    game_play_id        UUID UNIQUE NOT NULL REFERENCES public.game_plays(id) ON DELETE CASCADE,
    game_type           TEXT NOT NULL,
    win_level           TEXT NOT NULL,
    reward_tier_id      UUID REFERENCES public.reward_tiers(id) ON DELETE SET NULL,
    coupon_id           UUID UNIQUE REFERENCES public.coupons(id) ON DELETE SET NULL,
    whatsapp_sent       BOOLEAN NOT NULL DEFAULT FALSE,
    whatsapp_delivered  BOOLEAN,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_game_plays_user     ON public.game_plays  (user_id);
CREATE INDEX IF NOT EXISTS idx_game_plays_session  ON public.game_plays  (game_session_id);
CREATE INDEX IF NOT EXISTS idx_game_rewards_user   ON public.game_rewards (user_id);
CREATE INDEX IF NOT EXISTS idx_reward_tiers_lookup ON public.reward_tiers (game_type, win_level) WHERE is_active = TRUE;
