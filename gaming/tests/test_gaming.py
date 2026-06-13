import jwt
import uuid
from decimal import Decimal
from datetime import datetime, timezone as dt_timezone, timedelta
from django.test import TestCase, override_settings
from django.urls import reverse
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from gaming.models import RewardTier, GamePlay, GameReward
from coupons.models import Coupon
from inventory.models import Order, Profile


def generate_test_jwt(role, user_id="00000000-0000-0000-0000-000000000001", email="user@example.com"):
    """Helper: generate a valid JWT payload for the test environment."""
    payload = {
        "aud": "authenticated",
        "sub": user_id,
        "email": email,
        "app_metadata": {
            "role": role
        }
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


@override_settings(
    SUPABASE_JWT_SECRET="test-jwt-secret-key-at-least-32-chars-long",
    GAMING_SYSTEM_USER_ID="00000000-0000-0000-0000-000000000099"
)
class GamingAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user_uuid = "00000000-0000-0000-0000-000000000001"
        self.manager_token = generate_test_jwt("manager", self.user_uuid)
        self.staff_token = generate_test_jwt("staff", self.user_uuid)
        self.customer_token = generate_test_jwt("customer", self.user_uuid)

        # Create Profile for user
        Profile.objects.update_or_create(
            id=self.user_uuid,
            defaults={'role': 'customer', 'phone_number': '918765432109'}
        )

        self.now = timezone.now()

    def test_reward_tier_crud_permissions(self):
        """Only Managers can create, update, or list reward tiers."""
        list_url = reverse('reward-tier-list')
        payload = {
            "name": "Jackpot Tier",
            "game_type": "spin_wheel",
            "win_level": "jackpot",
            "discount_type": "percentage",
            "discount_value": "20.00",
            "max_discount_cap": "50.00",
            "valid_days": 15,
            "description": "Win 20% off!",
            "notify_whatsapp": True,
            "is_active": True
        }

        # Anonymous -> 401
        response = self.client.post(list_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # Customer -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.post(list_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Staff -> 403
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_token}")
        response = self.client.post(list_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Manager -> 201
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.post(list_url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tier_id = response.data["id"]

        # List tiers (Manager only)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.assertEqual(self.client.get(list_url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        self.assertEqual(self.client.get(list_url).status_code, status.HTTP_200_OK)

        # Detail/Update (Manager only)
        detail_url = reverse('reward-tier-detail', kwargs={'id': tier_id})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.assertEqual(self.client.patch(detail_url, data={"name": "New Name"}).status_code, status.HTTP_403_FORBIDDEN)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.manager_token}")
        response = self.client.patch(detail_url, data={"name": "New Name"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "New Name")

    def test_earn_calculation(self):
        """Test the plays earned from order history and ad watch calculations."""
        earn_url = reverse('gaming-earn')

        # 1. Initially, no orders, no ads
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.get(earn_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["order_plays_earned"], 0)
        self.assertEqual(response.data["order_plays_used"], 0)
        self.assertEqual(response.data["ad_plays_granted_today"], 0)
        self.assertEqual(response.data["total_plays_remaining"], 0)

        # 2. Add completed orders and check update
        Order.objects.create(user_id=self.user_uuid, total_amount=100, gst_amount=18, payment_method='UPI', payment_status='completed')
        Order.objects.create(user_id=self.user_uuid, total_amount=200, gst_amount=36, payment_method='card', payment_status='completed')
        # Create a pending/failed order (should not grant play)
        Order.objects.create(user_id=self.user_uuid, total_amount=50, gst_amount=9, payment_method='UPI', payment_status='pending')

        response = self.client.get(earn_url)
        self.assertEqual(response.data["order_plays_earned"], 2)
        self.assertEqual(response.data["order_plays_remaining"], 2)
        self.assertEqual(response.data["total_plays_remaining"], 2)

        # 3. Simulate playing the game (consuming one play)
        GamePlay.objects.create(user_id=self.user_uuid, game_session_id=str(uuid.uuid4()), game_type='spin_wheel', play_source='order')
        response = self.client.get(earn_url)
        self.assertEqual(response.data["order_plays_used"], 1)
        self.assertEqual(response.data["order_plays_remaining"], 1)
        self.assertEqual(response.data["total_plays_remaining"], 1)

    def test_ad_status_and_grant_flow(self):
        """Test daily ad play cap limits and status updates."""
        ad_status_url = reverse('gaming-ad-status')
        grant_ad_url = reverse('gaming-grant-ad-play')
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")

        # Initially, can watch ads
        response = self.client.get(ad_status_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["can_watch_ad"])
        self.assertEqual(response.data["ad_plays_granted_today"], 0)
        self.assertEqual(response.data["ad_plays_remaining_today"], 5)

        # Grant ad plays up to 5 times
        for i in range(1, 6):
            res = self.client.post(grant_ad_url, data={"ad_placement_id": "Rewarded_Android", "ad_unit_id": f"unit-{i}"})
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            self.assertTrue(res.data["granted"])
            self.assertEqual(res.data["ad_plays_granted_today"], i)
            self.assertEqual(res.data["ad_plays_remaining_today"], 5 - i)

        # 6th attempt -> 403 Forbidden
        res = self.client.post(grant_ad_url, data={"ad_placement_id": "Rewarded_Android", "ad_unit_id": "unit-6"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(res.data["granted"])
        self.assertIn("limit reached", res.data["detail"].lower())

        # Status check reflects full quota
        response = self.client.get(ad_status_url)
        self.assertFalse(response.data["can_watch_ad"])
        self.assertEqual(response.data["ad_plays_granted_today"], 5)
        self.assertEqual(response.data["ad_plays_remaining_today"], 0)

        # Check total remaining plays includes ad plays
        earn_response = self.client.get(reverse('gaming-earn'))
        self.assertEqual(earn_response.data["ad_plays_granted_today"], 5)
        self.assertEqual(earn_response.data["ad_plays_available_to_play"], 5)
        self.assertEqual(earn_response.data["total_plays_remaining"], 5)

    def test_ad_daily_reset_logic(self):
        """Test that ad plays quota resets when date transitions."""
        grant_ad_url = reverse('gaming-grant-ad-play')
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")

        # Grant 5 ad plays today
        for i in range(5):
            self.client.post(grant_ad_url, data={"ad_placement_id": "Rewarded_Android", "ad_unit_id": "xyz"})

        # Verify limit is reached
        res = self.client.post(grant_ad_url, data={"ad_placement_id": "Rewarded_Android", "ad_unit_id": "xyz"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Backdate the plays to yesterday
        yesterday = timezone.now() - timedelta(days=1)
        GamePlay.objects.filter(play_source='rewarded_ad').update(played_at=yesterday)

        # Now we should be able to watch more ads today!
        res = self.client.post(grant_ad_url, data={"ad_placement_id": "Rewarded_Android", "ad_unit_id": "xyz"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["granted"])
        self.assertEqual(res.data["ad_plays_granted_today"], 1)

    @patch('whatsapp.wa_client.send_text_message')
    def test_record_play_win_loss_flows(self, mock_whatsapp):
        """Test recording plays, win tier mapping, coupon generation, and idempotency."""
        record_url = reverse('gaming-record-play')
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")

        # 1. No plays remaining -> 403
        payload = {
            "game_session_id": "session-1",
            "game_type": "spin_wheel",
            "won": False
        }
        res = self.client.post(record_url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Grant 1 ad play to play
        reverse('gaming-grant-ad-play')
        self.client.post(reverse('gaming-grant-ad-play'), data={})

        # 3. Create a RewardTier for spin_wheel jackpot
        RewardTier.objects.create(
            name="Grand Prize",
            game_type="spin_wheel",
            win_level="jackpot",
            discount_type="percentage",
            discount_value=25.00,
            valid_days=7,
            notify_whatsapp=True,
            is_active=True
        )

        # 4. Play and lose
        loss_session = str(uuid.uuid4())
        payload = {
            "game_session_id": loss_session,
            "game_type": "spin_wheel",
            "won": False
        }
        res = self.client.post(record_url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["won"])
        self.assertIsNone(res.data["coupon_code"])
        self.assertEqual(res.data["plays_remaining"], 0)

        # Try to replay same session -> Idempotent response
        res = self.client.post(record_url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["won"])

        # 5. Grant another ad play and play to win
        self.client.post(reverse('gaming-grant-ad-play'), data={})
        win_session = str(uuid.uuid4())
        payload = {
            "game_session_id": win_session,
            "game_type": "spin_wheel",
            "won": True,
            "win_level": "jackpot"
        }
        res = self.client.post(record_url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["won"])
        self.assertIsNotNone(res.data["coupon_code"])
        self.assertEqual(res.data["coupon_discount_type"], "percentage")
        self.assertEqual(float(res.data["coupon_discount_value"]), 25.00)
        self.assertEqual(res.data["plays_remaining"], 0)

        # Check Coupon was created in database
        coupon = Coupon.objects.get(code=res.data["coupon_code"])
        self.assertEqual(coupon.discount_type, "percentage")
        self.assertEqual(coupon.discount_value, Decimal("25.00"))
        self.assertEqual(coupon.specific_user_id, uuid.UUID(self.user_uuid))

        # Check WhatsApp client was called
        mock_whatsapp.assert_called_once()
        self.assertIn(res.data["coupon_code"], mock_whatsapp.call_args[0][1])

        # Test idempotency on win
        res_dup = self.client.post(record_url, data=payload)
        self.assertEqual(res_dup.status_code, status.HTTP_200_OK)
        self.assertEqual(res_dup.data["coupon_code"], res.data["coupon_code"])

    def test_reward_tier_wildcard_fallback(self):
        """Test that reward tier matching falls back to 'any' wildcard if exact match is missing."""
        # Grant play
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        self.client.post(reverse('gaming-grant-ad-play'), data={})

        # Create wildcard RewardTier
        RewardTier.objects.create(
            name="General Reward",
            game_type="scratch_card",
            win_level="any",
            discount_type="flat_amount",
            discount_value=50.00,
            valid_days=10,
            notify_whatsapp=False,
            is_active=True
        )

        record_url = reverse('gaming-record-play')
        payload = {
            "game_session_id": str(uuid.uuid4()),
            "game_type": "scratch_card",
            "won": True,
            "win_level": "level_3"  # No exact match, falls back to 'any'
        }
        res = self.client.post(record_url, data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["won"])
        self.assertEqual(res.data["coupon_discount_type"], "flat_amount")
        self.assertEqual(float(res.data["coupon_discount_value"]), 50.00)

    def test_user_rewards_isolation(self):
        """Test list and detail view of user rewards, and that users cannot access others' rewards."""
        # Create rewards for user 1
        play1 = GamePlay.objects.create(user_id=self.user_uuid, game_session_id="s1", game_type="spin_wheel", play_source="order")
        coupon1 = Coupon.objects.create(code="USER1-COUPON", discount_type="flat_amount", discount_value=10, valid_from=self.now, created_by=uuid.uuid4())
        reward1 = GameReward.objects.create(user_id=self.user_uuid, game_play=play1, game_type="spin_wheel", win_level="jackpot", coupon=coupon1)

        # Create rewards for user 2
        user2_uuid = "00000000-0000-0000-0000-000000000002"
        play2 = GamePlay.objects.create(user_id=user2_uuid, game_session_id="s2", game_type="spin_wheel", play_source="order")
        coupon2 = Coupon.objects.create(code="USER2-COUPON", discount_type="flat_amount", discount_value=20, valid_from=self.now, created_by=uuid.uuid4())
        reward2 = GameReward.objects.create(user_id=user2_uuid, game_play=play2, game_type="spin_wheel", win_level="jackpot", coupon=coupon2)

        # Get list as customer 1
        list_url = reverse('gaming-reward-list')
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.customer_token}")
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only see user 1 reward
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["coupon"]["code"], "USER1-COUPON")

        # Get detail of own reward
        detail_url1 = reverse('gaming-reward-detail', kwargs={'id': reward1.id})
        response = self.client.get(detail_url1)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["coupon"]["code"], "USER1-COUPON")

        # Try to retrieve reward of user 2 -> 404 Not Found (due to isolation query filter)
        detail_url2 = reverse('gaming-reward-detail', kwargs={'id': reward2.id})
        response = self.client.get(detail_url2)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
