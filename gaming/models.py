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
    Created atomically when the user starts a spin/card/quiz or completes a rewarded ad watch.
    Tracks quota regardless of win or loss.
    """
    PLAY_SOURCE_CHOICES = [
        ('order', 'Order'),
        ('rewarded_ad', 'Rewarded Ad'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField()
    game_session_id = models.TextField(unique=True)   # Unity GUID or ad watch unique session ID
    game_type = models.TextField()
    play_source = models.TextField(choices=PLAY_SOURCE_CHOICES, default='order')
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
