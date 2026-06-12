# Spec 21 — Gaming Engine Integration & Coupon Rewards (Unity Mobile — Android/iOS)

## Goal

Integrate Dwarikas with a **Unity-built mobile game** (Android/iOS) for loyalty gamification.
The game authenticates players using the **same Supabase project** as the Dwarikas app — the
player identity is the same user in both systems. When a user wins in the game, Unity calls a
Supabase-JWT-authenticated endpoint on the Dwarikas backend, which auto-generates a single-use
coupon (via Spec 20), stores it against the user's account, and optionally delivers it via
WhatsApp (Spec 14). Users can view and redeem their reward coupons at checkout.

---

## Business Context

Gamification creates a customer engagement flywheel:

1. Customer shops → earns game plays (1 per completed order)
2. Customer wins the game → gets a reward coupon on their Dwarikas account
3. Coupon drives the next purchase → repeat

The Unity game is a **first-party app** developed and published by Dwarikas. It uses the same
Supabase Auth project, so the user identity is cryptographically verified — the backend does not
need to trust any externally asserted `user_id`.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                   SUPABASE AUTH (Shared)                         │
│     One auth system — Dwarikas App, Unity Game, Backend          │
└────────────┬────────────────────────────┬───────────────────────┘
             │                            │
             ▼                            ▼
  ┌──────────────────┐         ┌─────────────────────────┐
  │  Dwarikas App    │         │  Unity Mobile Game      │
  │  (Shopping)      │         │  (Android / iOS)        │
  │                  │         │                          │
  │  Same email &    │         │  Login with same         │
  │  password        │         │  email & password        │
  │  → Supabase JWT  │         │  → Supabase JWT          │
  └──────────────────┘         └──────────┬───────────────┘
                                          │
                          ┌───────────────┴──────────────────┐
                          │                                   │
                          ▼                                   ▼
             GET /gaming/earn/                POST /gaming/record-play/
             (how many plays left?)           (authorise a play + on win
                                              claim the coupon)
                          │                                   │
                          └───────────────┬──────────────────┘
                                          ▼
                               ┌──────────────────────┐
                               │  DWARIKAS BACKEND    │
                               │  (Django on Cloud Run)│
                               │                       │
                               │  Validates JWT ✅     │
                               │  Enforces play quota  │
                               │  Matches reward tier  │
                               │  Issues coupon        │
                               │  Sends WhatsApp       │
                               └──────────────────────┘
```

---

## Relationship to Existing Specs

| Spec | Impact |
|---|---|
| **Spec 20** (Coupon Codes) | `create_gaming_reward_coupon()` service is called on a win |
| **Spec 14** (WhatsApp Commerce Engine) | Coupon delivery via WhatsApp (optional per tier) |
| **Spec 03** (Authentication) | Unity uses the same Supabase JWT — validated by existing middleware |
| **Spec 04** (Database Models) | New `GamePlay`, `RewardTier`, `GameReward` models |

> **No external webhook.** There is no HMAC shared secret and no server-to-server webhook.
> The Unity game client calls the Dwarikas backend directly with the player's Supabase JWT.

---

## Scope

New Django app: `gaming/`

### Endpoints

| Method | Path | Actor | Description |
|---|---|---|---|
| `GET` | `/api/v1/gaming/earn/` | Authenticated Customer | Returns plays earned (from order history) and plays remaining |
| `POST` | `/api/v1/gaming/record-play/` | Authenticated Customer (Unity) | Authorises one play at game start; records a win and issues a coupon if `won=true` |
| `GET` | `/api/v1/gaming/rewards/` | Authenticated Customer | Lists all reward coupons earned by the calling user |
| `GET` | `/api/v1/gaming/rewards/<uuid:id>/` | Authenticated Customer | Single reward detail including coupon code |
| `POST` | `/api/v1/gaming/reward-tiers/` | Manager | Create a reward tier configuration |
| `GET` | `/api/v1/gaming/reward-tiers/` | Manager | List all reward tiers |
| `PATCH` | `/api/v1/gaming/reward-tiers/<uuid:id>/` | Manager | Update a reward tier |

---

## Play Quota Model

```
plays_earned  = COUNT of completed orders for the user  (server-computed)
plays_used    = COUNT of GamePlay records for the user   (server-tracked)
plays_remaining = plays_earned - plays_used
```

Unity calls `GET /gaming/earn/` before starting the game to show the player how many plays
they have. Before each spin/card/quiz, Unity calls `POST /gaming/record-play/` which:

1. Validates `plays_remaining > 0` — if not, returns HTTP 403.
2. Atomically creates a `GamePlay` record (consuming one play).
3. If `won=true` in the payload, also creates a `GameReward` and issues a coupon.
4. Returns the coupon code if a win was recorded.

This two-step design (authorise-then-play) prevents quota bypass: the server
allocates the play slot before the game outcome is decided client-side.

---

## Data Models

### `RewardTier`

Managers configure how wins translate into coupon values.

```
id                  UUID        PK — auto
name                TEXT        e.g., "Grand Prize", "Silver", "Bronze"
game_type           TEXT        e.g., "spin_wheel", "scratch_card", "quiz"
win_level           TEXT        Label from Unity (e.g., "jackpot", "level_2", "any")
discount_type       TEXT        'percentage' | 'flat_amount'
discount_value      DECIMAL(10,2) Value of the coupon reward
max_discount_cap    DECIMAL(10,2) Optional — cap for percentage rewards
valid_days          INTEGER     How many days the issued coupon is valid (default: 30)
description         TEXT        Optional — marketing description shown to user
notify_whatsapp     BOOLEAN     Whether to send the coupon via WhatsApp (default: true)
is_active           BOOLEAN     Default: true
created_at          TIMESTAMPTZ Auto
```

### `GamePlay`

One record per play consumed. Tracks quota usage regardless of win/loss.

```
id                  UUID        PK — auto
user_id             UUID        Supabase user who played
game_session_id     TEXT        Unique ID from Unity (GUID generated client-side)
game_type           TEXT        Type of game (e.g., "spin_wheel")
played_at           TIMESTAMPTZ Auto (created_at)
```

`game_session_id` has a UNIQUE constraint — prevents the same session being recorded twice
(idempotency if Unity retries on network failure).

### `GameReward`

One record per win event. Immutable audit log linking a play to its coupon.

```
id                  UUID        PK — auto
user_id             UUID        Supabase user who won
game_play_id        UUID (FK)   → GamePlay — the play session that resulted in this win
game_type           TEXT        Type of game
win_level           TEXT        Win level sent by Unity
reward_tier_id      UUID (FK)   → RewardTier — which tier was matched
coupon_id           UUID (FK)   → Coupon — the generated coupon
whatsapp_sent       BOOLEAN     Whether WhatsApp delivery was attempted
whatsapp_delivered  BOOLEAN     Nullable — delivery confirmation
created_at          TIMESTAMPTZ Auto
```

---

## Files to Create / Modify

### `gaming/` — New Django app

```bash
python manage.py startapp gaming
```

Add to `INSTALLED_APPS`:
```python
'gaming.apps.GamingConfig',
```

---

### `gaming/models.py`

```python
import uuid
from django.db import models


class RewardTier(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage'),
        ('flat_amount', 'Flat Amount'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    game_type = models.TextField()
    win_level = models.TextField()     # 'any' matches all win levels for that game_type
    discount_type = models.TextField(choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount_cap = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valid_days = models.IntegerField(default=30)
    description = models.TextField(null=True, blank=True)
    notify_whatsapp = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'reward_tiers'
        ordering = ['game_type', 'win_level']


class GamePlay(models.Model):
    """
    One record per play consumed by a user.
    Created atomically when the user starts a spin/card/quiz.
    Tracks quota regardless of win or loss.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField()
    game_session_id = models.TextField(unique=True)   # Unity GUID — deduplication key
    game_type = models.TextField()
    played_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'game_plays'
        ordering = ['-played_at']


class GameReward(models.Model):
    """
    Immutable record of every win event and the coupon issued.
    Created only when won=True in the record-play payload.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField()
    game_play = models.OneToOneField(
        GamePlay,
        on_delete=models.CASCADE,
        db_column='game_play_id',
        related_name='reward',
    )
    game_type = models.TextField()
    win_level = models.TextField()
    reward_tier = models.ForeignKey(
        RewardTier,
        on_delete=models.SET_NULL,
        null=True,
        db_column='reward_tier_id',
        related_name='rewards',
    )
    coupon = models.OneToOneField(
        'coupons.Coupon',
        on_delete=models.SET_NULL,
        null=True,
        db_column='coupon_id',
        related_name='game_reward',
    )
    whatsapp_sent = models.BooleanField(default=False)
    whatsapp_delivered = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'game_rewards'
        ordering = ['-created_at']
```

---

### `gaming/services.py`

```python
"""
Gaming service layer.
Handles play quota enforcement, reward tier matching, coupon generation, and WhatsApp delivery.
"""
import uuid
import logging
from django.db import transaction
from gaming.models import RewardTier, GamePlay, GameReward
from coupons.services import create_gaming_reward_coupon

logger = logging.getLogger(__name__)


def get_plays_remaining(user_id: uuid.UUID, plays_earned: int) -> int:
    """Returns how many plays the user still has available."""
    plays_used = GamePlay.objects.filter(user_id=user_id).count()
    return max(0, plays_earned - plays_used)


def find_matching_tier(game_type: str, win_level: str) -> RewardTier | None:
    """
    Finds the best matching RewardTier for a win event.
    Exact win_level match takes priority over 'any' wildcard.
    """
    tier = RewardTier.objects.filter(
        game_type=game_type,
        win_level=win_level,
        is_active=True,
    ).first()

    if not tier:
        tier = RewardTier.objects.filter(
            game_type=game_type,
            win_level='any',
            is_active=True,
        ).first()

    return tier


@transaction.atomic
def record_play(
    user_id: uuid.UUID,
    game_session_id: str,
    game_type: str,
    plays_earned: int,
    won: bool,
    win_level: str | None,
    system_user_id: uuid.UUID,
) -> dict:
    """
    Main entry point called by the Unity game on every play.

    Steps:
    1. Idempotency check — if game_session_id already exists, return existing result.
    2. Validate plays_remaining > 0.
    3. Create GamePlay record (consumes one play).
    4. If won=True: find tier, create coupon, create GameReward, send WhatsApp.

    Returns a dict with keys:
        play_id       — UUID of the GamePlay record
        plays_used    — updated count after this play
        plays_remaining — updated remaining count
        won           — bool
        reward        — GameReward instance or None
        coupon_code   — str or None
    """
    # Idempotency: if this session was already recorded, return existing outcome
    existing_play = GamePlay.objects.filter(game_session_id=game_session_id).first()
    if existing_play:
        logger.info(f'Duplicate game session {game_session_id} — returning existing result.')
        existing_reward = getattr(existing_play, 'reward', None)
        plays_used = GamePlay.objects.filter(user_id=user_id).count()
        return {
            'play_id': existing_play.id,
            'plays_used': plays_used,
            'plays_remaining': max(0, plays_earned - plays_used),
            'won': existing_reward is not None,
            'reward': existing_reward,
            'coupon_code': existing_reward.coupon.code if existing_reward and existing_reward.coupon else None,
        }

    # Validate play quota
    plays_used = GamePlay.objects.filter(user_id=user_id).count()
    plays_remaining = plays_earned - plays_used
    if plays_remaining <= 0:
        raise ValueError('No plays remaining for this user.')

    # Consume one play
    play = GamePlay.objects.create(
        user_id=user_id,
        game_session_id=game_session_id,
        game_type=game_type,
    )
    plays_used += 1
    plays_remaining -= 1

    reward = None
    coupon_code = None

    if won:
        if not win_level:
            logger.warning(f'won=True but no win_level provided for session {game_session_id}.')
        else:
            tier = find_matching_tier(game_type, win_level)
            if not tier:
                logger.warning(
                    f'No matching RewardTier for game_type={game_type}, win_level={win_level}.'
                )
            else:
                coupon = create_gaming_reward_coupon(
                    user_id=user_id,
                    reward_config={
                        'discount_type': tier.discount_type,
                        'discount_value': float(tier.discount_value),
                        'max_discount_cap': float(tier.max_discount_cap) if tier.max_discount_cap else None,
                        'valid_days': tier.valid_days,
                        'description': tier.description or f'{tier.name} reward from gaming',
                    },
                    created_by=system_user_id,
                )

                reward = GameReward.objects.create(
                    user_id=user_id,
                    game_play=play,
                    game_type=game_type,
                    win_level=win_level,
                    reward_tier=tier,
                    coupon=coupon,
                )
                coupon_code = coupon.code

                # WhatsApp notification — failure does NOT roll back the coupon
                if tier.notify_whatsapp:
                    try:
                        _send_whatsapp_reward_notification(user_id, coupon, tier)
                        GameReward.objects.filter(id=reward.id).update(whatsapp_sent=True)
                    except Exception as exc:
                        logger.error(f'WhatsApp notification failed for reward {reward.id}: {exc}')

    return {
        'play_id': play.id,
        'plays_used': plays_used,
        'plays_remaining': plays_remaining,
        'won': won and reward is not None,
        'reward': reward,
        'coupon_code': coupon_code,
    }


def _send_whatsapp_reward_notification(user_id, coupon, tier):
    """
    Sends a WhatsApp message to the user with their reward coupon code.
    No-op if the user has no phone number on their Profile.
    """
    from inventory.models import Profile
    from whatsapp.wa_client import WhatsAppClient

    try:
        profile = Profile.objects.get(id=user_id)
        phone = getattr(profile, 'phone_number', None)
        if not phone:
            return
    except Profile.DoesNotExist:
        return

    client = WhatsAppClient()
    message = (
        f"🎉 Congratulations! You won a reward from Dwarikas!\n\n"
        f"🎁 *{tier.name}*\n"
        f"Use code: *{coupon.code}*\n"
        f"Valid until: {coupon.valid_until.strftime('%d %b %Y') if coupon.valid_until else 'No expiry'}\n\n"
        f"Apply this code at checkout to redeem your discount. Happy shopping! 🛍️"
    )
    client.send_text(phone, message)
```

---

### `gaming/views.py`

```python
import uuid
from django.db import IntegrityError
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
import os

from api.permissions import IsManager
from gaming.models import RewardTier, GameReward
from gaming.serializers import RewardTierSerializer, GameRewardSerializer, RecordPlaySerializer
from gaming.services import record_play
from inventory.models import Order

# UUID of the system/service account used as `created_by` on auto-generated coupons.
# Should be a dedicated service account in Supabase auth.users.
GAMING_SYSTEM_USER_ID = os.environ.get('GAMING_SYSTEM_USER_ID', '00000000-0000-0000-0000-000000000000')


class GamePlaysEarnedView(APIView):
    """
    GET /api/v1/gaming/earn/

    Returns how many game plays the calling user has earned based on their
    completed order history, and how many plays they have remaining.

    Unity calls this before showing the game menu to display the play count.

    Response:
    {
        "user_id": "uuid",
        "plays_earned": 12,
        "plays_used": 10,
        "plays_remaining": 2,
        "plays_calculation": "1 play per confirmed order"
    }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = uuid.UUID(request.user.username)

        plays_earned = Order.objects.filter(
            user_id=user_id,
            payment_status='completed',
        ).count()

        from gaming.models import GamePlay
        plays_used = GamePlay.objects.filter(user_id=user_id).count()
        plays_remaining = max(0, plays_earned - plays_used)

        return Response({
            'user_id': str(user_id),
            'plays_earned': plays_earned,
            'plays_used': plays_used,
            'plays_remaining': plays_remaining,
            'plays_calculation': '1 play per confirmed order',
        })


class RecordPlayView(APIView):
    """
    POST /api/v1/gaming/record-play/

    Unity calls this on EVERY play (spin, scratch, quiz).
    - Validates the user has plays remaining.
    - Atomically records the play (consuming one quota slot).
    - If won=True, matches a reward tier and issues a coupon.

    Request payload:
    {
        "game_session_id": "<unity-generated-guid>",
        "game_type": "spin_wheel",
        "won": true,
        "win_level": "jackpot"   // required if won=true, omit or null if lost
    }

    Response (win):
    {
        "play_id": "uuid",
        "plays_remaining": 1,
        "won": true,
        "coupon_code": "GAME-XXXX",
        "coupon_discount_type": "percentage",
        "coupon_discount_value": "20.00",
        "coupon_valid_until": "2026-07-13"
    }

    Response (loss):
    {
        "play_id": "uuid",
        "plays_remaining": 1,
        "won": false,
        "coupon_code": null
    }

    Error (no plays left):  HTTP 403
    Error (bad payload):    HTTP 400
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RecordPlaySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user_id = uuid.UUID(request.user.username)
        system_user_id = uuid.UUID(GAMING_SYSTEM_USER_ID)

        # Get current plays earned to pass into service (avoids a second DB query there)
        plays_earned = Order.objects.filter(
            user_id=user_id,
            payment_status='completed',
        ).count()

        try:
            result = record_play(
                user_id=user_id,
                game_session_id=serializer.validated_data['game_session_id'],
                game_type=serializer.validated_data['game_type'],
                plays_earned=plays_earned,
                won=serializer.validated_data['won'],
                win_level=serializer.validated_data.get('win_level'),
                system_user_id=system_user_id,
            )
        except ValueError as exc:
            # No plays remaining
            return Response({'detail': str(exc)}, status=status.HTTP_403_FORBIDDEN)

        response_data = {
            'play_id': str(result['play_id']),
            'plays_remaining': result['plays_remaining'],
            'won': result['won'],
            'coupon_code': result['coupon_code'],
        }

        if result['reward'] and result['reward'].coupon:
            coupon = result['reward'].coupon
            response_data.update({
                'coupon_discount_type': coupon.discount_type,
                'coupon_discount_value': str(coupon.discount_value),
                'coupon_valid_until': coupon.valid_until.date().isoformat() if coupon.valid_until else None,
            })

        return Response(response_data, status=status.HTTP_200_OK)


class UserRewardListView(generics.ListAPIView):
    """
    GET /api/v1/gaming/rewards/
    Returns all gaming rewards (with coupon codes) earned by the calling user.
    """
    serializer_class = GameRewardSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user_id = uuid.UUID(self.request.user.username)
        return GameReward.objects.filter(user_id=user_id).select_related('coupon', 'reward_tier')


class UserRewardDetailView(generics.RetrieveAPIView):
    """
    GET /api/v1/gaming/rewards/<uuid:id>/
    """
    serializer_class = GameRewardSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'

    def get_queryset(self):
        user_id = uuid.UUID(self.request.user.username)
        return GameReward.objects.filter(user_id=user_id).select_related('coupon', 'reward_tier')


class RewardTierListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/gaming/reward-tiers/  — Manager
    POST /api/v1/gaming/reward-tiers/  — Manager
    """
    serializer_class = RewardTierSerializer
    permission_classes = [IsManager]
    queryset = RewardTier.objects.order_by('game_type', 'win_level')


class RewardTierDetailView(generics.RetrieveUpdateAPIView):
    """
    GET / PATCH /api/v1/gaming/reward-tiers/<uuid:id>/
    """
    serializer_class = RewardTierSerializer
    permission_classes = [IsManager]
    queryset = RewardTier.objects.all()
    lookup_field = 'id'
```

---

### `gaming/serializers.py`

```python
from rest_framework import serializers
from gaming.models import RewardTier, GameReward
from coupons.serializers import CouponSerializer


class RecordPlaySerializer(serializers.Serializer):
    game_session_id = serializers.CharField(max_length=255)
    game_type = serializers.CharField(max_length=100)
    won = serializers.BooleanField()
    win_level = serializers.CharField(max_length=100, required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get('won') and not attrs.get('win_level'):
            raise serializers.ValidationError(
                {'win_level': 'win_level is required when won=true.'}
            )
        return attrs


class RewardTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = RewardTier
        fields = [
            'id', 'name', 'game_type', 'win_level', 'discount_type',
            'discount_value', 'max_discount_cap', 'valid_days', 'description',
            'notify_whatsapp', 'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class GameRewardSerializer(serializers.ModelSerializer):
    coupon = CouponSerializer(read_only=True)
    reward_tier_name = serializers.CharField(source='reward_tier.name', read_only=True)

    class Meta:
        model = GameReward
        fields = [
            'id', 'game_type', 'win_level', 'reward_tier_name',
            'coupon', 'whatsapp_sent', 'whatsapp_delivered', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']
```

---

### `gaming/urls.py`

```python
from django.urls import path
from gaming.views import (
    GamePlaysEarnedView,
    RecordPlayView,
    UserRewardListView,
    UserRewardDetailView,
    RewardTierListCreateView,
    RewardTierDetailView,
)

urlpatterns = [
    path('gaming/earn/',                    GamePlaysEarnedView.as_view(),      name='gaming-earn'),
    path('gaming/record-play/',             RecordPlayView.as_view(),           name='gaming-record-play'),
    path('gaming/rewards/',                 UserRewardListView.as_view(),       name='gaming-reward-list'),
    path('gaming/rewards/<uuid:id>/',       UserRewardDetailView.as_view(),     name='gaming-reward-detail'),
    path('gaming/reward-tiers/',            RewardTierListCreateView.as_view(), name='reward-tier-list'),
    path('gaming/reward-tiers/<uuid:id>/',  RewardTierDetailView.as_view(),     name='reward-tier-detail'),
]
```

---

### Supabase Migration

```sql
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
```

---

## Environment Variables Required

| Variable | Description |
|---|---|
| `GAMING_SYSTEM_USER_ID` | UUID of the system account used as `created_by` for auto-generated gaming reward coupons. A dedicated service account in Supabase auth. |

> **Removed:** `GAMING_WEBHOOK_SECRET` is no longer required. Authentication is handled by
> Supabase JWT — the same middleware already in place for all other endpoints.

---

## Unity Integration Contract

### Unity SDK Setup (C#)

```csharp
// Package Manager → Add by git URL:
// https://github.com/supabase-community/supabase-csharp.git
//
// Same Supabase URL and anon key as the Dwarikas app.

var supabase = new Supabase.Client(
    "https://your-project.supabase.co",
    "your-anon-key",
    new SupabaseOptions { AutoRefreshToken = true }
);
await supabase.InitializeAsync();
```

### Step 1 — Authenticate the Player

```csharp
// Player logs in with the same email/password as the Dwarikas shopping app.
var session = await supabase.Auth.SignIn(email, password);
string jwt = session.AccessToken;   // Valid Supabase JWT — reused for all API calls
```

> The player's identity in Unity is **identical** to their identity in the Dwarikas app.
> No separate account or mapping table is required.

### Step 2 — Check Plays Remaining (Before Showing Game)

```csharp
var request = UnityWebRequest.Get("https://api.dwarikas.com/api/v1/gaming/earn/");
request.SetRequestHeader("Authorization", $"Bearer {jwt}");
await request.SendWebRequest();

// Response: { "plays_remaining": 3, "plays_earned": 12, "plays_used": 9 }
var data = JsonUtility.FromJson<PlaysResponse>(request.downloadHandler.text);
if (data.plays_remaining <= 0)
    ShowNoPlaysScreen();
else
    ShowGameMenuWith(data.plays_remaining);
```

### Step 3 — Record a Play on Every Spin / Card / Quiz

```csharp
// Generate a unique session ID on the device before each play.
string sessionId = System.Guid.NewGuid().ToString();

// Determine outcome with your Unity game logic (spin result, scratch reveal, etc.)
bool playerWon = spinResult == WinningSegment;
string winLevel = playerWon ? "jackpot" : null;

var payload = JsonUtility.ToJson(new RecordPlayPayload {
    game_session_id = sessionId,
    game_type       = "spin_wheel",
    won             = playerWon,
    win_level       = winLevel,
});

var request = new UnityWebRequest(
    "https://api.dwarikas.com/api/v1/gaming/record-play/",
    "POST"
);
request.uploadHandler   = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(payload));
request.downloadHandler = new DownloadHandlerBuffer();
request.SetRequestHeader("Authorization", $"Bearer {jwt}");
request.SetRequestHeader("Content-Type",  "application/json");

await request.SendWebRequest();

if (request.responseCode == 200) {
    var result = JsonUtility.FromJson<RecordPlayResponse>(request.downloadHandler.text);
    if (result.won) {
        ShowWinScreen(result.coupon_code, result.coupon_discount_value);
        // e.g., "🎉 You won! Use code GAME-XXXX for 20% off!"
    } else {
        ShowLossScreen(result.plays_remaining);
    }
} else if (request.responseCode == 403) {
    ShowNoPlaysScreen(); // plays exhausted
}
```

### Step 4 — Player Views Rewards in Dwarikas App

The player opens the Dwarikas shopping app and navigates to "My Rewards". The app calls
`GET /api/v1/gaming/rewards/` with the same Supabase JWT and shows all earned coupons.
Coupons are applied at checkout via the standard `apply-coupon` endpoint from Spec 20.

---

## Full Win Flow Sequence

```
Unity Game (Android/iOS)           Dwarikas Backend             WhatsApp → Player
       |                                  |                            |
       |-- Auth: supabase.SignIn() -----> |  (Supabase Auth — shared) |
       |<-- Supabase JWT -----------------|                            |
       |                                  |                            |
       |-- GET /gaming/earn/ -----------> |                            |
       |<-- { plays_remaining: 3 } -------|                            |
       |                                  |                            |
       |   [Player spins — wins jackpot]  |                            |
       |                                  |                            |
       |-- POST /gaming/record-play/ ---> |                            |
       |   { session_id, game_type,       |                            |
       |     won: true,                   |   validate JWT → user_id   |
       |     win_level: "jackpot" }       |   check plays_remaining    |
       |                                  |   create GamePlay          |
       |                                  |   find_matching_tier()     |
       |                                  |   create_gaming_reward_coupon()
       |                                  |   create GameReward        |
       |                                  |   send WhatsApp ---------> |
       |<-- { won: true,                  |   "You won! GAME-XXXX"     |
       |      coupon_code: "GAME-XXXX",   |                            |
       |      plays_remaining: 2 } -------|                            |
       |                                  |                            |
       |   [Show win screen in Unity]     |                            |
       |                                  |                            |
Player opens Dwarikas App                 |                            |
       |                                  |                            |
       |-- GET /gaming/rewards/ --------> |                            |
       |<-- [{ coupon: { code: "GAME-XXXX", discount: "20%" } }]      |
       |                                  |                            |
       |-- POST /checkout/apply-coupon/ { coupon_code: "GAME-XXXX" }  |
       |<-- { discount_amount: "50.00", final_total: "90.00" }        |
```

---

## Security Model

| Concern | How it is handled |
|---|---|
| **User identity** | Extracted from Supabase JWT — never trusted from request body |
| **Play quota abuse** | Server enforces `plays_remaining > 0` before recording any play |
| **Replay attacks** | `game_session_id` (Unity GUID) has a UNIQUE DB constraint — duplicate calls are idempotent |
| **Win spoofing (calling API without playing)** | Server enforces quota: each call consumes a play. Worst case: user claims best-tier coupon. Coupon value is capped by `RewardTier.max_discount_cap` |
| **JWT expiry** | Supabase SDK auto-refreshes tokens (`AutoRefreshToken = true`) |
| **Man-in-the-middle** | All traffic over HTTPS — Cloud Run enforces TLS |

> **Note on win spoofing:** A technically sophisticated user could call `/gaming/record-play/`
> with `won=true` directly from a REST client, bypassing the Unity game entirely. The risk is
> bounded: they consume one of their earned plays per fake win, and the maximum coupon value
> is controlled by the `RewardTier` configuration. For a Phase 1 loyalty program this is an
> acceptable risk. Phase 2 can add server-side game outcome validation if needed.

---

## Acceptance Criteria

- [ ] `POST /api/v1/gaming/record-play/` with valid JWT, `won=true`, and valid `win_level` creates a `GamePlay`, `GameReward`, and single-use user-specific `Coupon`
- [ ] `POST /api/v1/gaming/record-play/` with `won=false` creates only a `GamePlay` — no coupon issued
- [ ] Calling `record-play/` with a duplicate `game_session_id` returns the existing result without creating new records (idempotent)
- [ ] `record-play/` returns HTTP 403 when `plays_remaining <= 0`
- [ ] `record-play/` returns HTTP 400 when `won=true` but `win_level` is missing
- [ ] `GET /api/v1/gaming/earn/` returns correct `plays_earned`, `plays_used`, `plays_remaining`
- [ ] `win_level: "any"` in `RewardTier` correctly acts as wildcard fallback when no exact match exists
- [ ] `GET /api/v1/gaming/rewards/` returns only the calling user's rewards with coupon codes
- [ ] WhatsApp notification is sent when `RewardTier.notify_whatsapp=true` and user has a phone number
- [ ] WhatsApp delivery failure does NOT roll back the coupon — reward is preserved regardless
- [ ] Generated coupon can be applied at checkout via Spec 20 `apply-coupon` endpoint
- [ ] All endpoints reject requests without a valid Supabase JWT (HTTP 401)
- [ ] Full test suite: play quota enforcement, idempotency, tier matching (exact + wildcard), coupon generation, WhatsApp delivery, plays count calculation

---

## Django App Layout

```
gaming/
├── __init__.py
├── apps.py
├── models.py           # RewardTier, GamePlay, GameReward
├── serializers.py      # RecordPlaySerializer, RewardTierSerializer, GameRewardSerializer
├── services.py         # record_play(), find_matching_tier(), get_plays_remaining()
├── views.py
├── urls.py
└── tests/
    ├── __init__.py
    ├── test_record_play.py     # quota enforcement, idempotency, win/loss paths
    ├── test_rewards.py         # reward list/detail views
    ├── test_reward_tiers.py    # manager CRUD
    └── test_earn.py            # plays calculation
```

---

## Unity Project Notes

- **Target platforms:** Android API 24+, iOS 14+
- **Supabase SDK:** `supabase-community/supabase-csharp` (Unity-compatible)
- **Supabase project:** Same project URL and anon key as the Dwarikas app
- **Auth method:** Email + password (same credentials as Dwarikas account)
- **API calls:** `UnityWebRequest` with `Authorization: Bearer <jwt>` header
- **Session ID generation:** `System.Guid.NewGuid().ToString()` — generated client-side before each play, sent to server for deduplication
