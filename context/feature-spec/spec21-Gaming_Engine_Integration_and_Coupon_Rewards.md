# Spec 21 — Gaming Engine Integration & Coupon Rewards

## Goal

Integrate Dwarikas with an external gaming engine that runs loyalty games (spin-the-wheel, scratch cards, quiz challenges, etc.). When a user wins a game, the gaming engine notifies the Dwarikas backend via a secure webhook. The backend then auto-generates a single-use coupon (via the Coupon service from Spec 20), stores it against the user's account, and optionally delivers it via WhatsApp (Spec 14). Users can then view their reward coupons and redeem them at checkout.

---

## Business Context

Gamification is a proven customer engagement strategy. By tying the gaming experience directly to the shopping journey, Dwarikas creates a flywheel:

1. Customer shops → earns game plays
2. Customer wins game → gets a reward coupon
3. Coupon drives next purchase → repeat

The backend acts as the **trusted authority** for reward issuance. The gaming engine (an external SaaS or custom app) is not trusted to create or modify coupons directly — it only signals a win event. The Dwarikas backend generates the actual coupon value according to configurable reward tiers.

---

## Relationship to Existing Specs

| Spec | Impact |
|---|---|
| **Spec 20** (Coupon Codes) | `create_gaming_reward_coupon()` service function is called to generate the reward coupon |
| **Spec 14** (WhatsApp Commerce Engine) | Coupon delivery via WhatsApp message (optional, configurable per reward tier) |
| **Spec 03** (Authentication) | Gaming engine webhook uses HMAC-SHA256 shared secret, not Supabase JWT |
| **Spec 04** (Database Models) | New `GameReward` and `RewardTier` models |

---

## Scope

New Django app: `gaming/`

### Endpoints

| Method | Path | Actor | Description |
|---|---|---|---|
| `POST` | `/api/v1/gaming/webhook/` | External Gaming Engine | Secure webhook: signals a win event and triggers coupon issuance |
| `GET` | `/api/v1/gaming/rewards/` | Authenticated Customer | Lists all reward coupons earned by the calling user |
| `GET` | `/api/v1/gaming/rewards/<uuid:id>/` | Authenticated Customer | Single reward detail (includes coupon code) |
| `POST` | `/api/v1/gaming/reward-tiers/` | Manager | Create a reward tier configuration |
| `GET` | `/api/v1/gaming/reward-tiers/` | Manager | List all reward tiers |
| `PATCH` | `/api/v1/gaming/reward-tiers/<uuid:id>/` | Manager | Update a reward tier |
| `GET` | `/api/v1/gaming/earn/` | Authenticated Customer | Returns how many game plays the calling user has earned (based on their order history) |

---

## Data Models

### `RewardTier`

Managers configure how wins translate into coupon values. Multiple tiers allow different rewards for different win levels (e.g., "Grand Prize", "Runner Up", "Participation").

```
id                  UUID        PK — auto
name                TEXT        e.g., "Grand Prize", "Silver", "Bronze"
game_type           TEXT        e.g., "spin_wheel", "scratch_card", "quiz" — from gaming engine
win_level           TEXT        Label from gaming engine (e.g., "jackpot", "level_2", "any")
discount_type       TEXT        'percentage' | 'flat_amount'
discount_value      DECIMAL(10,2) Value of the coupon reward
max_discount_cap    DECIMAL(10,2) Optional — cap for percentage rewards
valid_days          INTEGER     How many days the issued coupon is valid (default: 30)
description         TEXT        Optional — marketing description shown to user
notify_whatsapp     BOOLEAN     Whether to send the coupon via WhatsApp (default: true)
is_active           BOOLEAN     Default: true
created_at          TIMESTAMPTZ Auto
```

### `GameReward`

Immutable record of every game win event and the coupon issued.

```
id                  UUID        PK — auto
user_id             UUID        Supabase user who won
game_session_id     TEXT        Unique ID from the gaming engine (for deduplication)
game_type           TEXT        Type of game (e.g., "spin_wheel")
win_level           TEXT        Win level from the gaming engine payload
reward_tier_id      UUID (FK)   → RewardTier — which tier was matched
coupon_id           UUID (FK)   → Coupon — the generated coupon
whatsapp_sent       BOOLEAN     Whether WhatsApp delivery was attempted
whatsapp_delivered  BOOLEAN     Nullable — delivery confirmation
raw_payload         JSONB       Full webhook payload for audit
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


class GameReward(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField()
    game_session_id = models.TextField(unique=True)  # Deduplication key
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
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'game_rewards'
        ordering = ['-created_at']
```

---

### `gaming/services.py` — Reward resolution and coupon issuance

```python
"""
Gaming engine reward service.
Handles win event processing, reward tier matching, coupon generation, and WhatsApp delivery.
"""
import uuid
import logging
from django.db import transaction
from gaming.models import RewardTier, GameReward
from coupons.services import create_gaming_reward_coupon

logger = logging.getLogger(__name__)


def find_matching_tier(game_type: str, win_level: str) -> RewardTier | None:
    """
    Finds the best matching RewardTier for a game win event.
    Exact win_level match takes priority over 'any' wildcard.
    """
    # Try exact match first
    tier = RewardTier.objects.filter(
        game_type=game_type,
        win_level=win_level,
        is_active=True,
    ).first()

    if not tier:
        # Fall back to wildcard tier
        tier = RewardTier.objects.filter(
            game_type=game_type,
            win_level='any',
            is_active=True,
        ).first()

    return tier


@transaction.atomic
def process_win_event(
    user_id: uuid.UUID,
    game_session_id: str,
    game_type: str,
    win_level: str,
    raw_payload: dict,
    system_user_id: uuid.UUID,  # UUID of the system/manager account used as coupon creator
) -> GameReward | None:
    """
    Main entry point for processing a win event from the gaming engine.

    1. Checks for duplicate game_session_id (idempotency).
    2. Finds the matching RewardTier.
    3. Generates a coupon via the Coupon service.
    4. Creates a GameReward record.
    5. Sends WhatsApp notification if configured.

    Returns the created GameReward, or the existing one if already processed.
    """
    # Idempotency: if this session was already processed, return existing reward
    existing = GameReward.objects.filter(game_session_id=game_session_id).first()
    if existing:
        logger.info(f'Duplicate win event for session {game_session_id} — returning existing reward.')
        return existing

    tier = find_matching_tier(game_type, win_level)
    if not tier:
        logger.warning(f'No matching RewardTier for game_type={game_type}, win_level={win_level}. No coupon issued.')
        return None

    # Generate the coupon
    coupon = create_gaming_reward_coupon(
        user_id=user_id,
        reward_config={
            'discount_type': tier.discount_type,
            'discount_value': float(tier.discount_value),
            'valid_days': tier.valid_days,
            'description': tier.description or f'{tier.name} reward from gaming',
        },
        created_by=system_user_id,
    )

    reward = GameReward.objects.create(
        user_id=user_id,
        game_session_id=game_session_id,
        game_type=game_type,
        win_level=win_level,
        reward_tier=tier,
        coupon=coupon,
        raw_payload=raw_payload,
    )

    # Send WhatsApp notification (non-blocking — failure doesn't roll back coupon issuance)
    if tier.notify_whatsapp:
        try:
            _send_whatsapp_reward_notification(user_id, coupon, tier)
            GameReward.objects.filter(id=reward.id).update(whatsapp_sent=True)
        except Exception as exc:
            logger.error(f'WhatsApp notification failed for reward {reward.id}: {exc}')

    return reward


def _send_whatsapp_reward_notification(user_id, coupon, tier):
    """
    Sends a WhatsApp message to the user with their reward coupon code.
    Uses the WhatsApp client from Spec 14.

    NOTE: This requires the user's WhatsApp phone number to be stored in their profile.
    If the profile has no phone number, this is a no-op.
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

### `gaming/webhook.py` — Webhook signature verification

```python
"""
Gaming engine webhook signature verification.
The gaming engine signs its requests with HMAC-SHA256 using a shared secret.
"""
import hashlib
import hmac
import os


GAMING_WEBHOOK_SECRET = os.environ.get('GAMING_WEBHOOK_SECRET', '')


def verify_gaming_webhook_signature(request_body: bytes, signature_header: str) -> bool:
    """
    Verifies the HMAC-SHA256 signature from the gaming engine.
    Expected header: X-Gaming-Signature: sha256=<hex_digest>
    Bypassed if GAMING_WEBHOOK_SECRET is not set (local dev).
    """
    if not GAMING_WEBHOOK_SECRET:
        return True  # Bypass in local dev

    if not signature_header or not signature_header.startswith('sha256='):
        return False

    expected = hmac.new(
        GAMING_WEBHOOK_SECRET.encode('utf-8'),
        request_body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header[len('sha256='):]
    return hmac.compare_digest(expected, received)
```

---

### `gaming/views.py`

```python
import json
import os
import uuid
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.permissions import IsManager
from gaming.models import RewardTier, GameReward
from gaming.serializers import RewardTierSerializer, GameRewardSerializer
from gaming.services import process_win_event
from gaming.webhook import verify_gaming_webhook_signature

# A system-level UUID for coupons auto-created by the gaming engine
# Should map to a dedicated system account in Supabase auth.users
GAMING_SYSTEM_USER_ID = os.environ.get('GAMING_SYSTEM_USER_ID', '00000000-0000-0000-0000-000000000000')


@method_decorator(csrf_exempt, name='dispatch')
class GamingWebhookView(APIView):
    """
    POST /api/v1/gaming/webhook/

    Receives win events from the external gaming engine.
    Signature-verified via HMAC-SHA256 (X-Gaming-Signature header).

    Expected payload:
    {
        "event": "win",
        "game_session_id": "unique-session-id",
        "game_type": "spin_wheel",
        "win_level": "jackpot",
        "user_id": "<supabase-user-uuid>",
        "metadata": {}   // optional extra data, stored in raw_payload
    }
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        # Verify signature
        signature = request.headers.get('X-Gaming-Signature', '')
        if not verify_gaming_webhook_signature(request.body, signature):
            return HttpResponse(status=401)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

        event = payload.get('event')
        if event != 'win':
            # Non-win events (e.g., game_started, game_ended) are acknowledged but ignored
            return HttpResponse(status=200)

        game_session_id = payload.get('game_session_id')
        game_type = payload.get('game_type')
        win_level = payload.get('win_level')
        user_id_str = payload.get('user_id')

        if not all([game_session_id, game_type, win_level, user_id_str]):
            return HttpResponse(status=400)

        try:
            user_id = uuid.UUID(user_id_str)
        except (ValueError, TypeError):
            return HttpResponse(status=400)

        system_user_id = uuid.UUID(GAMING_SYSTEM_USER_ID)

        reward = process_win_event(
            user_id=user_id,
            game_session_id=game_session_id,
            game_type=game_type,
            win_level=win_level,
            raw_payload=payload,
            system_user_id=system_user_id,
        )

        # Always return 200 to the gaming engine to prevent retries
        return HttpResponse(status=200)


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
    GET /PATCH /api/v1/gaming/reward-tiers/<uuid:id>/
    """
    serializer_class = RewardTierSerializer
    permission_classes = [IsManager]
    queryset = RewardTier.objects.all()
    lookup_field = 'id'


class GamePlaysEarnedView(APIView):
    """
    GET /api/v1/gaming/earn/

    Returns how many game plays the calling user has earned based on their order history.
    The formula is: 1 game play per confirmed order.

    The gaming engine can call this endpoint (with the user's JWT forwarded) to determine
    how many plays to grant in the gaming interface.

    Response:
    {
        "user_id": "uuid",
        "total_orders": 12,
        "plays_earned": 12,
        "plays_calculation": "1 play per confirmed order"
    }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from inventory.models import Order
        user_id = uuid.UUID(request.user.username)
        total_orders = Order.objects.filter(
            user_id=user_id,
            payment_status='completed',
        ).count()

        return Response({
            'user_id': str(user_id),
            'total_orders': total_orders,
            'plays_earned': total_orders,
            'plays_calculation': '1 play per confirmed order',
        })
```

---

### `gaming/serializers.py`

```python
from rest_framework import serializers
from gaming.models import RewardTier, GameReward
from coupons.serializers import CouponSerializer


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
    GamingWebhookView,
    UserRewardListView,
    UserRewardDetailView,
    RewardTierListCreateView,
    RewardTierDetailView,
    GamePlaysEarnedView,
)

urlpatterns = [
    path('gaming/webhook/', GamingWebhookView.as_view(), name='gaming-webhook'),
    path('gaming/rewards/', UserRewardListView.as_view(), name='gaming-reward-list'),
    path('gaming/rewards/<uuid:id>/', UserRewardDetailView.as_view(), name='gaming-reward-detail'),
    path('gaming/reward-tiers/', RewardTierListCreateView.as_view(), name='reward-tier-list'),
    path('gaming/reward-tiers/<uuid:id>/', RewardTierDetailView.as_view(), name='reward-tier-detail'),
    path('gaming/earn/', GamePlaysEarnedView.as_view(), name='gaming-earn'),
]
```

---

### Supabase Migration

```sql
-- Reward tiers (configures what coupons are issued per win level)
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

-- Game rewards (audit log of every win event and issued coupon)
CREATE TABLE IF NOT EXISTS public.game_rewards (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    game_session_id     TEXT UNIQUE NOT NULL,  -- Deduplication key from gaming engine
    game_type           TEXT NOT NULL,
    win_level           TEXT NOT NULL,
    reward_tier_id      UUID REFERENCES public.reward_tiers(id) ON DELETE SET NULL,
    coupon_id           UUID UNIQUE REFERENCES public.coupons(id) ON DELETE SET NULL,
    whatsapp_sent       BOOLEAN NOT NULL DEFAULT FALSE,
    whatsapp_delivered  BOOLEAN,
    raw_payload         JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_game_rewards_user ON public.game_rewards (user_id);
CREATE INDEX IF NOT EXISTS idx_game_rewards_session ON public.game_rewards (game_session_id);
CREATE INDEX IF NOT EXISTS idx_reward_tiers_lookup ON public.reward_tiers (game_type, win_level) WHERE is_active = TRUE;
```

---

## Environment Variables Required

| Variable | Description |
|---|---|
| `GAMING_WEBHOOK_SECRET` | Shared HMAC-SHA256 secret between Dwarikas backend and the gaming engine. Store in Google Secret Manager. |
| `GAMING_SYSTEM_USER_ID` | UUID of the system account used as `created_by` for auto-generated gaming reward coupons. Can be a dedicated service account in Supabase auth. |

---

## Integration Contract with the External Gaming Engine

The gaming engine must:
1. **Signal wins** by `POST`ing to `/api/v1/gaming/webhook/` with the payload below and `X-Gaming-Signature: sha256=<hmac>` header.
2. **Retrieve play entitlements** by making a `GET /api/v1/gaming/earn/` call with the user's Supabase JWT to determine how many plays to grant.

### Webhook Payload Schema

```json
{
  "event": "win",
  "game_session_id": "sess_abc123",
  "game_type": "spin_wheel",
  "win_level": "jackpot",
  "user_id": "<supabase-user-uuid>",
  "metadata": {
    "game_name": "Eid Spin",
    "spin_result": "Golden Star"
  }
}
```

### Supported `event` values

| Event | Action |
|---|---|
| `win` | Triggers coupon issuance |
| `game_started` | Acknowledged, ignored |
| `game_ended` | Acknowledged, ignored |
| Anything else | Acknowledged, ignored |

### Response Contract

The backend **always** returns HTTP 200 to the gaming engine (including error cases), to prevent infinite retry loops. If the event is invalid or duplicate, it is silently discarded.

---

## Full Win Flow Sequence

```
Gaming Engine              Dwarikas Backend             WhatsApp → User
     |                           |                            |
     |-- POST /gaming/webhook/ ->|                            |
     |   { event: "win",         |                            |
     |     user_id, session_id,  |                            |
     |     game_type, win_level } |                           |
     |                    verify HMAC signature               |
     |                    find_matching_tier()                |
     |                    create_gaming_reward_coupon()       |
     |                    create GameReward record            |
     |                    send WhatsApp notification -------> |
     |<-- HTTP 200 --------------|   "You won! Code: GAMExxxxx" |
     |                           |                            |
     |                                                        |
Customer opens Dwarikas App                                   |
     |                           |                            |
     |-- GET /gaming/rewards/ -->|                            |
     |<-- [{ coupon: { code: "GAMExxxxx", ... } }]           |
     |                           |                            |
     |-- POST /checkout/apply-coupon/ { coupon_code: "GAMExxxxx" }
     |<-- { discount_amount: "50.00", final_total: "90.00" } |
```

---

## Acceptance Criteria

- [ ] `POST /api/v1/gaming/webhook/` with valid HMAC signature and `event: "win"` creates a `GameReward` and a single-use user-specific `Coupon`
- [ ] Duplicate `game_session_id` is handled idempotently — no duplicate coupon issued
- [ ] Invalid HMAC signature returns HTTP 401
- [ ] Non-`win` events return HTTP 200 without side effects
- [ ] `win_level: "any"` in `RewardTier` correctly acts as wildcard fallback when no exact match exists
- [ ] `GET /api/v1/gaming/rewards/` returns only the calling user's rewards with coupon codes
- [ ] WhatsApp notification is sent when `RewardTier.notify_whatsapp = true` and user has a phone number in Profile
- [ ] WhatsApp delivery failure does NOT roll back the coupon — reward is preserved regardless
- [ ] `GET /api/v1/gaming/earn/` returns correct play count based on confirmed orders
- [ ] Generated coupon from gaming engine can be applied at checkout via Spec 20 `apply-coupon` endpoint
- [ ] Full test suite: webhook signature, idempotency, tier matching, coupon generation, WhatsApp delivery, play count

---

## Django App Layout

```
gaming/
├── __init__.py
├── apps.py
├── models.py
├── serializers.py
├── services.py          # process_win_event(), find_matching_tier()
├── webhook.py           # verify_gaming_webhook_signature()
├── views.py
├── urls.py
└── tests/
    ├── __init__.py
    ├── test_webhook.py
    ├── test_rewards.py
    └── test_reward_tiers.py
```
