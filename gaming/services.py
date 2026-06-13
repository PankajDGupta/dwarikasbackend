import uuid
import logging
from django.db import transaction
from django.utils import timezone
from datetime import timedelta, datetime

from gaming.models import RewardTier, GamePlay, GameReward
from coupons.services import create_gaming_reward_coupon
from inventory.models import Order

logger = logging.getLogger(__name__)


def get_plays_data(user_id: uuid.UUID) -> dict:
    """
    Computes game play quota statistics for a user.
    Uses:
      - completed orders count (1 play per order)
      - total game plays (by source)
      - ad plays cap (5 per day limit)
    """
    # 1. Order plays calculation
    order_plays_earned = Order.objects.filter(
        user_id=user_id,
        payment_status='completed',
    ).count()

    order_plays_used = GamePlay.objects.filter(
        user_id=user_id,
        play_source='order',
    ).exclude(game_type='rewarded_ad').count()

    order_plays_remaining = max(0, order_plays_earned - order_plays_used)

    # 2. Ad plays watch limit calculation (max 5 ads watched per day)
    today = timezone.now().date()
    ad_plays_granted_today = GamePlay.objects.filter(
        user_id=user_id,
        play_source='rewarded_ad',
        game_type='rewarded_ad',
        played_at__date=today,
    ).count()

    ad_plays_limit_per_day = 5
    ad_plays_remaining_today = max(0, ad_plays_limit_per_day - ad_plays_granted_today)

    # 3. Ad plays currently available to play
    total_ad_plays_granted = GamePlay.objects.filter(
        user_id=user_id,
        play_source='rewarded_ad',
        game_type='rewarded_ad',
    ).count()

    total_ad_plays_used = GamePlay.objects.filter(
        user_id=user_id,
        play_source='rewarded_ad',
    ).exclude(game_type='rewarded_ad').count()

    ad_plays_available_to_play = max(0, total_ad_plays_granted - total_ad_plays_used)

    # 4. Total remaining plays
    total_plays_remaining = order_plays_remaining + ad_plays_available_to_play

    return {
        'user_id': str(user_id),
        'order_plays_earned': order_plays_earned,
        'order_plays_used': order_plays_used,
        'order_plays_remaining': order_plays_remaining,
        'ad_plays_granted_today': ad_plays_granted_today,
        'ad_plays_remaining_today': ad_plays_remaining_today,
        'ad_plays_limit_per_day': ad_plays_limit_per_day,
        'ad_plays_available_to_play': ad_plays_available_to_play,
        'total_plays_remaining': total_plays_remaining,
        'plays_calculation': '1 play per confirmed order + up to 5 ad plays per day',
    }


def get_ad_status(user_id: uuid.UUID) -> dict:
    """
    Returns ad status indicating if the user can watch another rewarded ad today,
    current counters, and when the limit resets.
    """
    plays_data = get_plays_data(user_id)
    now = timezone.now()
    today = now.date()
    next_day = today + timedelta(days=1)
    resets_at = timezone.make_aware(datetime.combine(next_day, datetime.min.time()))

    return {
        'can_watch_ad': plays_data['ad_plays_remaining_today'] > 0,
        'ad_plays_granted_today': plays_data['ad_plays_granted_today'],
        'ad_plays_remaining_today': plays_data['ad_plays_remaining_today'],
        'ad_plays_limit_per_day': plays_data['ad_plays_limit_per_day'],
        'resets_at': resets_at.isoformat(),
    }


@transaction.atomic
def grant_ad_play(user_id: uuid.UUID, ad_placement_id: str | None = None, ad_unit_id: str | None = None) -> dict:
    """
    Grants an ad-derived play slot to the user after they successfully watch a rewarded ad.
    Creates a GamePlay record to represent the ad grant (using game_type='rewarded_ad').
    Enforces the daily limit of 5.
    """
    plays_data = get_plays_data(user_id)
    if plays_data['ad_plays_remaining_today'] <= 0:
        raise ValueError('Daily ad play limit reached. Come back tomorrow!')

    # Unique identifier for the ad watch session
    game_session_id = f"ad-grant-{uuid.uuid4()}"

    # Record the ad watch
    GamePlay.objects.create(
        user_id=user_id,
        game_session_id=game_session_id,
        game_type='rewarded_ad',
        play_source='rewarded_ad',
    )

    updated_plays = get_plays_data(user_id)
    return {
        'granted': True,
        'ad_plays_granted_today': updated_plays['ad_plays_granted_today'],
        'ad_plays_remaining_today': updated_plays['ad_plays_remaining_today'],
        'total_plays_remaining': updated_plays['total_plays_remaining'],
    }


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
    won: bool,
    win_level: str | None,
    system_user_id: uuid.UUID,
) -> dict:
    """
    Consumes a play quota slot and optionally generates a coupon reward on win.
    """
    # 1. Idempotency: if this session was already recorded, return existing outcome
    existing_play = GamePlay.objects.filter(game_session_id=game_session_id).first()
    if existing_play:
        logger.info(f'Duplicate game session {game_session_id} — returning existing result.')
        existing_reward = getattr(existing_play, 'reward', None)
        updated_plays = get_plays_data(user_id)
        return {
            'play_id': existing_play.id,
            'plays_remaining': updated_plays['total_plays_remaining'],
            'won': existing_reward is not None,
            'reward': existing_reward,
            'coupon_code': existing_reward.coupon.code if existing_reward and existing_reward.coupon else None,
        }

    # 2. Check plays remaining
    plays_data = get_plays_data(user_id)
    if plays_data['total_plays_remaining'] <= 0:
        raise ValueError('No plays remaining for this user.')

    # 3. Determine play source to consume: order play takes priority, then ad play
    if plays_data['order_plays_remaining'] > 0:
        play_source = 'order'
    else:
        play_source = 'rewarded_ad'

    # Create GamePlay record
    play = GamePlay.objects.create(
        user_id=user_id,
        game_session_id=game_session_id,
        game_type=game_type,
        play_source=play_source,
    )

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

    updated_plays = get_plays_data(user_id)
    return {
        'play_id': play.id,
        'plays_remaining': updated_plays['total_plays_remaining'],
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
    from whatsapp.wa_client import send_text_message

    try:
        profile = Profile.objects.get(id=user_id)
        phone = getattr(profile, 'phone_number', None)
        if not phone:
            return
    except Profile.DoesNotExist:
        return

    message = (
        f"🎉 Congratulations! You won a reward from Dwarikas!\n\n"
        f"🎁 *{tier.name}*\n"
        f"Use code: *{coupon.code}*\n"
        f"Valid until: {coupon.valid_until.strftime('%d %b %Y') if coupon.valid_until else 'No expiry'}\n\n"
        f"Apply this code at checkout to redeem your discount. Happy shopping! 🛍️"
    )
    send_text_message(phone, message)
