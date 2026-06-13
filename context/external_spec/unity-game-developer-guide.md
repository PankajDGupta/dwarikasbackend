# Dwarikas Unity Game — Developer Integration Guide

**Audience:** Unity game developers building the Dwarikas loyalty game (Android / iOS)  
**Backend:** Dwarikas API (Django / Cloud Run)  
**Auth system:** Supabase (shared with the Dwarikas shopping app)  
**Ad SDK:** Unity LevelPlay (rewarded video + interstitial)  
**Last updated:** 2026-06-13

---

## Overview

The Dwarikas loyalty game is a **first-party Unity mobile game** (Android + iOS) that rewards
customers for shopping. Players earn game plays in two ways:

1. **By shopping** — 1 play per completed order in the Dwarikas app *(unlimited)*
2. **By watching rewarded ads** — 1 play per fully-watched video ad *(max 5 per day)*

When a player wins a game (spin-the-wheel, scratch card, quiz, etc.), the backend automatically
issues a discount coupon to their Dwarikas account.

As a Unity developer, your responsibilities are:

1. **Authenticate** the player using the same Supabase project as the Dwarikas app.
2. **Integrate Unity LevelPlay** for rewarded video and interstitial ads.
3. **Check** how many game plays the player has remaining before showing the game.
4. **Allow players to watch ads** to earn bonus plays when they run out.
5. **Run** the game and determine the outcome (win / loss) using Unity game logic.
6. **Report** the outcome to the Dwarikas backend.
7. **Display** the coupon code to the player if they won.

The backend handles coupon generation, WhatsApp notification, and reward storage.

---

## Table of Contents

1. [Project Credentials](#1-project-credentials)
2. [Unity SDK Setup](#2-unity-sdk-setup)
3. [Authentication Flow](#3-authentication-flow)
4. [Ad Monetization — Unity LevelPlay](#4-ad-monetization--unity-levelplay)
5. [API Reference](#5-api-reference)
6. [Complete Game Flow — Step by Step](#6-complete-game-flow--step-by-step)
7. [C# Code Samples](#7-c-code-samples)
8. [Game Types and Win Levels](#8-game-types-and-win-levels)
9. [Error Handling Reference](#9-error-handling-reference)
10. [Network Retry and Idempotency](#10-network-retry-and-idempotency)
11. [Testing Against the Backend](#11-testing-against-the-backend)
12. [UI/UX Guidelines](#12-uiux-guidelines)

---

## 1. Project Credentials

> **These values will be provided to you by the Dwarikas backend team before development starts.**
> Do NOT hardcode production credentials in source control.

| Credential | Description | Where to get it |
|---|---|---|
| `SUPABASE_URL` | Your Supabase project URL | Provided by backend team |
| `SUPABASE_ANON_KEY` | Public anon key for the Supabase project | Provided by backend team |
| `DWARIKAS_API_BASE_URL` | Base URL of the Dwarikas API | e.g., `https://api.dwarikas.com` |
| `LEVELPLAY_APP_KEY` | Unity LevelPlay app key | Unity LevelPlay Dashboard |
| `REWARDED_PLACEMENT_ID` | Placement ID for rewarded video ad unit | Unity LevelPlay Dashboard |
| `INTERSTITIAL_PLACEMENT_ID` | Placement ID for interstitial ad unit | Unity LevelPlay Dashboard |

Store all credentials in a Unity `ScriptableObject` excluded from source control via `.gitignore`.
**Never commit API keys or ad keys to git.**

```csharp
[CreateAssetMenu(fileName = "GameConfig", menuName = "Dwarikas/Game Config")]
public class GameConfig : ScriptableObject
{
    [Header("Supabase")]
    public string SupabaseUrl;
    public string SupabaseAnonKey;

    [Header("Dwarikas API")]
    public string DwarikasApiBaseUrl;

    [Header("Unity LevelPlay")]
    public string LevelPlayAppKey;
    public string RewardedPlacementId;
    public string InterstitialPlacementId;
}
```

---

## 2. Unity SDK Setup

### 2.1 — Supabase C# SDK

Add the Supabase community SDK to your Unity project.

**Via Package Manager → Add by git URL:**
```
https://github.com/supabase-community/supabase-csharp.git
```

**Or via NuGet for Unity:**
```
Supabase
Supabase.Gotrue
```

**Minimum Unity version:** 2019.4 LTS  
**Supported platforms:** Android (API 24+), iOS (14+)

### 2.2 — Unity LevelPlay SDK

LevelPlay is Unity's built-in ad mediation platform (formerly IronSource). It handles
both rewarded video and interstitial ads.

**Via Unity Package Manager → Add by name:**
```
com.unity.services.levelplay
```

Or download directly from the [Unity LevelPlay Integration Manager](https://developers.is.com/ironsource-mobile/unity/unity-plugin/).

**LevelPlay Dashboard Setup (do this before coding):**
1. Sign in to [LevelPlay Dashboard](https://platform.ironsrc.com)
2. Create a new App → select Android / iOS
3. Get your **App Key** → store as `LEVELPLAY_APP_KEY`
4. Create Ad Unit → **Rewarded Video** → get Placement ID → store as `REWARDED_PLACEMENT_ID`
5. Create Ad Unit → **Interstitial** → get Placement ID → store as `INTERSTITIAL_PLACEMENT_ID`
6. Add ad networks (Unity Ads is included by default; also add AdMob via mediation for higher fill rate)

### 2.3 — Initialise Both SDKs

Create a single `GameBootstrap` MonoBehaviour that initialises everything at app start.

```csharp
using Supabase;
using IronSourceSDK;
using UnityEngine;

public class GameBootstrap : MonoBehaviour
{
    public static GameBootstrap Instance { get; private set; }

    [SerializeField] private GameConfig config;

    public Supabase.Client Supabase  { get; private set; }
    public bool            IsReady   { get; private set; }

    async void Awake()
    {
        if (Instance != null) { Destroy(gameObject); return; }
        Instance = this;
        DontDestroyOnLoad(gameObject);

        // ── Supabase ─────────────────────────────────────────────────────
        var options = new SupabaseOptions
        {
            AutoRefreshToken = true,  // keeps JWT alive automatically
            PersistSession   = true,  // saves session across app restarts
        };
        Supabase = new Supabase.Client(config.SupabaseUrl, config.SupabaseAnonKey, options);
        await Supabase.InitializeAsync();

        // ── Unity LevelPlay ───────────────────────────────────────────────
        IronSource.Agent.init(config.LevelPlayAppKey,
                              IronSourceAdUnits.REWARDED_VIDEO,
                              IronSourceAdUnits.INTERSTITIAL);

        // Set the Supabase user ID so Unity's S2S callback can identify the player
        if (Supabase.Auth.CurrentUser != null)
            SetLevelPlayUserId(Supabase.Auth.CurrentUser.Id);

        IsReady = true;
    }

    public void SetLevelPlayUserId(string supabaseUserId)
    {
        // This ID is passed in Unity's server-to-server ad verification callback
        LevelPlay.SetDynamicUserId(supabaseUserId);
    }

    public string AccessToken => Supabase.Auth.CurrentSession?.AccessToken;
    public bool   IsLoggedIn  => Supabase.Auth.CurrentUser != null;
}
```

---

## 3. Authentication Flow

### Key Principle

The player uses the **same email and password** for the Unity game and the Dwarikas
shopping app. Both share one Supabase project. After login, the JWT is accepted by
all Dwarikas backend endpoints.

> **Important:** After login, call `SetLevelPlayUserId()` so Unity's ad system knows
> which Supabase user is playing. This is required for server-side ad verification.

### Login

```csharp
public async Task<bool> LoginAsync(string email, string password)
{
    try
    {
        var session = await GameBootstrap.Instance.Supabase.Auth.SignIn(email, password);
        if (session?.AccessToken == null) return false;

        // CRITICAL: Update LevelPlay user ID immediately after login
        GameBootstrap.Instance.SetLevelPlayUserId(session.User.Id);
        return true;
    }
    catch (Exception ex)
    {
        Debug.LogError($"Login failed: {ex.Message}");
        return false;
    }
}
```

### Session Persistence

With `PersistSession = true`, the player will not need to log in every time.
On app launch, `InitializeAsync()` restores the previous session automatically.
After restore, always refresh the LevelPlay user ID:

```csharp
// In your post-init check:
if (GameBootstrap.Instance.IsLoggedIn)
    GameBootstrap.Instance.SetLevelPlayUserId(
        GameBootstrap.Instance.Supabase.Auth.CurrentUser.Id
    );
```

### Logout

```csharp
await GameBootstrap.Instance.Supabase.Auth.SignOut();
LevelPlay.SetDynamicUserId(null);  // clear user from ad system
```

---

## 4. Ad Monetization — Unity LevelPlay

### 4.1 — Ad Strategy Overview

| Ad Type | When Shown | Player Action | Play Grant | Revenue |
|---|---|---|---|---|
| **Rewarded Video** | Player taps "Watch Ad for a free play" | Voluntary — player chooses to watch | +1 play (max 5/day) | Highest eCPM |
| **Interstitial** | After a loss screen, before lobby returns | Automatic — player sees it passively | None | Medium eCPM |

> **No banner ads.** Banners are excluded — they degrade the premium feel of the game
> and generate negligible revenue compared to rewarded and interstitial formats.

### 4.2 — Daily Ad Play Cap

Players can earn a maximum of **5 bonus plays per day** through rewarded ads.
This cap is enforced by the backend — the game must still call `GET /gaming/ad-status/`
to check the current quota and reflect it in the UI accurately.

The cap resets at **midnight local time** on the server (IST).

### 4.3 — Rewarded Video Ad Flow

```
Player taps "Watch Ad for a free play"
        │
        ├─ Call GET /gaming/ad-status/  ← check quota before loading
        │
        ├─ can_watch_ad = false → show "Daily limit reached" toast
        │
        ├─ can_watch_ad = true → call rewardedAd.LoadAd()
        │
        ├─ OnAdLoaded → call rewardedAd.ShowAd()
        │
        ├─ Player watches full ad
        │
        ├─ OnAdRewarded fires (full watch confirmed by Unity SDK)
        │
        ├─ Call POST /gaming/grant-ad-play/ (JWT auth)
        │
        ├─ 200 OK → show "+1 play earned!" animation
        │           update lobby play count
        │           reload next ad in background
        │
        └─ 403 → show "Daily limit reached" (race condition edge case)
```

### 4.4 — Interstitial Ad Flow

```
Player loses a game
        │
        ├─ Show loss screen for 2 seconds
        │
        ├─ interstitialAd.IsAdReady() ?
        │   ├─ YES → show interstitial
        │   │         OnAdClosed → navigate to lobby
        │   └─ NO  → navigate to lobby immediately (no delay)
        │
        └─ Pre-load next interstitial in background
```

> **Never show an interstitial after a win.** Win screens should feel celebratory and
> uninterrupted. Interstitials only appear on the loss-to-lobby transition.

### 4.5 — Ad Manager Class (Full Implementation)

```csharp
using System;
using System.Threading.Tasks;
using IronSourceSDK;
using UnityEngine;

public class AdManager : MonoBehaviour
{
    public static AdManager Instance { get; private set; }

    [SerializeField] private GameConfig config;

    // Events for UI to listen to
    public event Action<int>  OnAdPlayGranted;    // fires with new plays_remaining count
    public event Action       OnDailyLimitReached;
    public event Action       OnInterstitialClosed;

    private LevelPlayRewardedAd     _rewardedAd;
    private LevelPlayInterstitialAd _interstitialAd;
    private DwarikasApiClient       _api;

    void Awake()
    {
        if (Instance != null) { Destroy(gameObject); return; }
        Instance = this;
        DontDestroyOnLoad(gameObject);
    }

    void Start()
    {
        _api = new DwarikasApiClient(
            config.DwarikasApiBaseUrl,
            () => GameBootstrap.Instance.AccessToken
        );

        InitialiseRewardedAd();
        InitialiseInterstitialAd();
    }

    // ─── REWARDED VIDEO ──────────────────────────────────────────────────────

    void InitialiseRewardedAd()
    {
        _rewardedAd = new LevelPlayRewardedAd(config.RewardedPlacementId);

        _rewardedAd.OnAdLoaded     += info => Debug.Log("Rewarded ad loaded.");
        _rewardedAd.OnAdLoadFailed += error => Debug.LogWarning($"Rewarded load failed: {error.ErrorMessage}");
        _rewardedAd.OnAdRewarded   += OnRewardedAdCompleted;
        _rewardedAd.OnAdShowFailed += error => Debug.LogWarning($"Rewarded show failed: {error.ErrorMessage}");

        _rewardedAd.LoadAd();
    }

    public async Task ShowRewardedAdAsync()
    {
        // 1. Check quota before wasting a loaded ad on a capped user
        AdStatusResponse status;
        try { status = await _api.GetAdStatusAsync(); }
        catch (Exception ex)
        {
            Debug.LogWarning($"Could not check ad status: {ex.Message}");
            return;
        }

        if (!status.CanWatchAd)
        {
            OnDailyLimitReached?.Invoke();
            return;
        }

        // 2. Show the ad
        if (_rewardedAd.IsAdReady())
            _rewardedAd.ShowAd(config.RewardedPlacementId);
        else
        {
            Debug.LogWarning("Rewarded ad not ready. Reloading.");
            _rewardedAd.LoadAd();
        }
    }

    async void OnRewardedAdCompleted(LevelPlayAdInfo adInfo, LevelPlayReward reward)
    {
        // OnAdRewarded only fires after the player watches the FULL ad
        try
        {
            var result = await _api.GrantAdPlayAsync(adInfo.AdUnitId);

            if (result.Granted)
                OnAdPlayGranted?.Invoke(result.TotalPlaysRemaining);
            else
                OnDailyLimitReached?.Invoke();
        }
        catch (NoPlaysRemainingException)
        {
            OnDailyLimitReached?.Invoke();
        }
        catch (Exception ex)
        {
            Debug.LogError($"grant-ad-play failed: {ex.Message}");
        }
        finally
        {
            // Always pre-load the next rewarded ad
            _rewardedAd.LoadAd();
        }
    }

    // ─── INTERSTITIAL ────────────────────────────────────────────────────────

    void InitialiseInterstitialAd()
    {
        _interstitialAd = new LevelPlayInterstitialAd(config.InterstitialPlacementId);

        _interstitialAd.OnAdLoaded     += info => Debug.Log("Interstitial loaded.");
        _interstitialAd.OnAdLoadFailed += error => Debug.LogWarning($"Interstitial load failed: {error.ErrorMessage}");
        _interstitialAd.OnAdClosed     += info =>
        {
            OnInterstitialClosed?.Invoke();
            _interstitialAd.LoadAd();   // pre-load next one immediately
        };

        _interstitialAd.LoadAd();
    }

    /// <summary>
    /// Call this after the loss screen. If an interstitial is ready it will show;
    /// if not, fires OnInterstitialClosed immediately so the game can continue.
    /// </summary>
    public void ShowInterstitialAfterLoss()
    {
        if (_interstitialAd.IsAdReady())
            _interstitialAd.ShowAd();
        else
        {
            // Ad not loaded — don't block the player, proceed to lobby
            OnInterstitialClosed?.Invoke();
            _interstitialAd.LoadAd();
        }
    }
}
```

---

## 5. API Reference

**Base URL:** `{DWARIKAS_API_BASE_URL}/api/v1`

**Authentication:** Every request must include the Supabase JWT in the `Authorization` header:
```
Authorization: Bearer <supabase_access_token>
Content-Type: application/json
```

> All endpoints return HTTP **401** if the JWT is missing or expired. Redirect to login screen.

---

### 5.1 — GET `/gaming/earn/`

**Purpose:** Check all plays available to the player (from orders and from ad plays today).

**Call when:** On the game lobby screen, before showing the "Play" button.

**Request:**
```
GET {BASE_URL}/api/v1/gaming/earn/
Authorization: Bearer <jwt>
```

**Success Response — HTTP 200:**
```json
{
    "user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "order_plays_earned": 12,
    "order_plays_used": 10,
    "order_plays_remaining": 2,
    "ad_plays_granted_today": 2,
    "ad_plays_used_today": 1,
    "ad_plays_remaining_today": 1,
    "ad_plays_limit_per_day": 5,
    "total_plays_remaining": 3,
    "plays_calculation": "1 play per confirmed order + up to 5 ad plays per day"
}
```

| Field | Type | Description |
|---|---|---|
| `order_plays_remaining` | int | Plays from completed Dwarikas orders |
| `ad_plays_remaining_today` | int | Bonus plays available from ads today |
| `ad_plays_limit_per_day` | int | Daily ad play cap (currently 5) |
| `total_plays_remaining` | int | **Use this to decide if "Play Now" is enabled** |

**UI logic:**
- `total_plays_remaining > 0` → "Play Now" button enabled
- `total_plays_remaining == 0` → "No plays left" state
- `ad_plays_remaining_today > 0` → "Watch Ad" button enabled
- `ad_plays_remaining_today == 0` → "Watch Ad" button greyed out

---

### 5.2 — GET `/gaming/ad-status/`

**Purpose:** Check ad play quota before loading a rewarded ad. Lightweight call —
use this instead of `/gaming/earn/` when you only need ad quota info.

**Request:**
```
GET {BASE_URL}/api/v1/gaming/ad-status/
Authorization: Bearer <jwt>
```

**Success Response — HTTP 200:**
```json
{
    "can_watch_ad": true,
    "ad_plays_granted_today": 2,
    "ad_plays_remaining_today": 3,
    "ad_plays_limit_per_day": 5,
    "resets_at": "2026-06-14T00:00:00+05:30"
}
```

| Field | Type | Description |
|---|---|---|
| `can_watch_ad` | boolean | **Check this first** — `false` means daily cap reached |
| `ad_plays_remaining_today` | int | How many more ad plays are available today |
| `resets_at` | string (ISO 8601) | When the daily cap resets — show this in the UI |

---

### 5.3 — POST `/gaming/grant-ad-play/`

**Purpose:** Tell the backend the player just watched a full rewarded ad and should receive
+1 play. Call this inside `OnAdRewarded` — which Unity SDK only fires after a **complete** watch.

**Request:**
```
POST {BASE_URL}/api/v1/gaming/grant-ad-play/
Authorization: Bearer <jwt>
Content-Type: application/json
```

```json
{
    "ad_placement_id": "Rewarded_Android",
    "ad_unit_id": "abc123xyz"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `ad_placement_id` | string | ✅ | Your LevelPlay placement ID (from `GameConfig`) |
| `ad_unit_id` | string | ✅ | Ad unit ID from `LevelPlayAdInfo.AdUnitId` |

**Success Response — HTTP 200 (play granted):**
```json
{
    "granted": true,
    "ad_plays_granted_today": 3,
    "ad_plays_remaining_today": 2,
    "total_plays_remaining": 4
}
```

**Error Response — HTTP 403 (daily limit reached):**
```json
{
    "granted": false,
    "detail": "Daily ad play limit reached. Come back tomorrow!",
    "resets_at": "2026-06-14T00:00:00+05:30"
}
```

> **When to call:** Only inside `OnAdRewarded`. Never call this after `OnAdShowFailed`,
> `OnAdClosed` without a reward, or any other lifecycle event.

---

### 5.4 — POST `/gaming/record-play/`

**Purpose:** Report a game outcome (win or loss). Call this after every spin/card/quiz,
regardless of the play source (order or ad).

**Request:**
```
POST {BASE_URL}/api/v1/gaming/record-play/
Authorization: Bearer <jwt>
Content-Type: application/json
```

```json
{
    "game_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "game_type": "spin_wheel",
    "won": true,
    "win_level": "jackpot"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `game_session_id` | string | ✅ Always | GUID generated by Unity before each play. See [Section 10](#10-network-retry-and-idempotency). |
| `game_type` | string | ✅ Always | Game type — must match configured values. See [Section 8](#8-game-types-and-win-levels). |
| `won` | boolean | ✅ Always | `true` if player won, `false` if lost. |
| `win_level` | string | ✅ If `won=true` | Prize tier label. Required when `won=true`, omit/null if lost. |

**Response — Win — HTTP 200:**
```json
{
    "play_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "plays_remaining": 1,
    "won": true,
    "coupon_code": "GAME-A3XK9P",
    "coupon_discount_type": "percentage",
    "coupon_discount_value": "20.00",
    "coupon_valid_until": "2026-07-13"
}
```

**Response — Loss — HTTP 200:**
```json
{
    "play_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "plays_remaining": 2,
    "won": false,
    "coupon_code": null
}
```

**Error Responses:**

| Code | Meaning |
|---|---|
| `400` | Bad payload (missing fields) |
| `401` | JWT expired → redirect to login |
| `403` | No plays remaining |

---

### 5.5 — GET `/gaming/rewards/`

**Purpose:** List all reward coupons the player has earned from the game.

**Request:**
```
GET {BASE_URL}/api/v1/gaming/rewards/
Authorization: Bearer <jwt>
```

**Response — HTTP 200:** Array of reward objects (same shape as a single reward below).

---

### 5.6 — GET `/gaming/rewards/<id>/`

**Purpose:** Single reward detail.

```
GET {BASE_URL}/api/v1/gaming/rewards/{reward_uuid}/
Authorization: Bearer <jwt>
```

**Response — HTTP 200:**
```json
{
    "id": "uuid",
    "game_type": "spin_wheel",
    "win_level": "jackpot",
    "reward_tier_name": "Grand Prize",
    "coupon": {
        "code": "GAME-A3XK9P",
        "discount_type": "percentage",
        "discount_value": "20.00",
        "valid_until": "2026-07-13T23:59:59Z",
        "is_used": false
    },
    "whatsapp_sent": true,
    "created_at": "2026-06-13T10:23:45Z"
}
```

---

## 6. Complete Game Flow — Step by Step

```
APP LAUNCH
    │
    ├─ Restore Supabase session (auto via SDK)
    ├─ Is player logged in?
    │   ├─ NO  → Login Screen → SignIn() → SetLevelPlayUserId()
    │   └─ YES → SetLevelPlayUserId() → Game Lobby
    │
GAME LOBBY
    │
    ├─ Call GET /gaming/earn/
    ├─ Call GET /gaming/ad-status/         (parallel with earn/)
    │
    ├─ Render lobby:
    │   ├─ "You have {total_plays_remaining} plays"
    │   ├─ [Play Now]     → enabled if total_plays_remaining > 0
    │   └─ [Watch Ad ▶]  → enabled if can_watch_ad = true
    │                        greyed out if can_watch_ad = false
    │                        shows "Resets at {resets_at}" when capped
    │
PLAYER TAPS "WATCH AD"
    │
    ├─ AdManager.ShowRewardedAdAsync()
    │   ├─ GET /gaming/ad-status/  → confirm still available
    │   ├─ Show rewarded video
    │   ├─ OnAdRewarded → POST /gaming/grant-ad-play/
    │   └─ 200 OK → animate "+1 Play!" → refresh lobby count
    │
PLAYER TAPS "PLAY NOW"
    │
    ├─ game_session_id = Guid.NewGuid().ToString()   ← generate BEFORE game runs
    ├─ Store game_session_id in PlayerPrefs (crash safety)
    ├─ Run game animation / logic
    │
GAME OUTCOME DETERMINED
    │
    ├─ WIN  → won=true, win_level = "jackpot" / "silver" / etc.
    │          POST /gaming/record-play/ → show Win Screen (no ad)
    │
    └─ LOSS → won=false
               POST /gaming/record-play/ → show Loss Screen (2 sec)
               AdManager.ShowInterstitialAfterLoss()
               OnInterstitialClosed → return to Lobby
```

---

## 7. C# Code Samples

### Dwarikas API Client (full)

```csharp
using System;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;

public class DwarikasApiClient
{
    private readonly string    _baseUrl;
    private readonly Func<string> _getToken;

    public DwarikasApiClient(string baseUrl, Func<string> getToken)
    {
        _baseUrl  = baseUrl.TrimEnd('/');
        _getToken = getToken;
    }

    // ── GET /gaming/earn/ ────────────────────────────────────────────────────

    public async Task<PlaysResponse> GetPlaysAsync()
    {
        var req = UnityWebRequest.Get($"{_baseUrl}/api/v1/gaming/earn/");
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");
        await req.SendWebRequest();
        ThrowOnError(req);
        return JsonUtility.FromJson<PlaysResponse>(req.downloadHandler.text);
    }

    // ── GET /gaming/ad-status/ ───────────────────────────────────────────────

    public async Task<AdStatusResponse> GetAdStatusAsync()
    {
        var req = UnityWebRequest.Get($"{_baseUrl}/api/v1/gaming/ad-status/");
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");
        await req.SendWebRequest();
        ThrowOnError(req);
        return JsonUtility.FromJson<AdStatusResponse>(req.downloadHandler.text);
    }

    // ── POST /gaming/grant-ad-play/ ──────────────────────────────────────────

    public async Task<GrantAdPlayResponse> GrantAdPlayAsync(string adUnitId)
    {
        var payload = JsonUtility.ToJson(new GrantAdPlayRequest
        {
            ad_placement_id = GameBootstrap.Instance.GetComponent<GameConfig>().RewardedPlacementId,
            ad_unit_id      = adUnitId,
        });

        var req = new UnityWebRequest($"{_baseUrl}/api/v1/gaming/grant-ad-play/", "POST");
        req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");
        req.SetRequestHeader("Content-Type",  "application/json");

        await req.SendWebRequest();

        if (req.responseCode == 403) throw new NoPlaysRemainingException();
        ThrowOnError(req);

        return JsonUtility.FromJson<GrantAdPlayResponse>(req.downloadHandler.text);
    }

    // ── POST /gaming/record-play/ ────────────────────────────────────────────

    public async Task<RecordPlayResponse> RecordPlayAsync(RecordPlayRequest payload)
    {
        string json = JsonUtility.ToJson(payload);
        var req = new UnityWebRequest($"{_baseUrl}/api/v1/gaming/record-play/", "POST");
        req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");
        req.SetRequestHeader("Content-Type",  "application/json");

        await req.SendWebRequest();

        if (req.responseCode == 403) throw new NoPlaysRemainingException();
        ThrowOnError(req);

        return JsonUtility.FromJson<RecordPlayResponse>(req.downloadHandler.text);
    }

    // ── GET /gaming/rewards/ ─────────────────────────────────────────────────

    public async Task<Reward[]> GetRewardsAsync()
    {
        var req = UnityWebRequest.Get($"{_baseUrl}/api/v1/gaming/rewards/");
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");
        await req.SendWebRequest();
        ThrowOnError(req);
        // Unity's JsonUtility doesn't support root arrays — wrap in a helper
        return JsonHelper.FromJsonArray<Reward>(req.downloadHandler.text);
    }

    // ─────────────────────────────────────────────────────────────────────────

    private void ThrowOnError(UnityWebRequest req)
    {
        if (req.responseCode == 401) throw new UnauthorizedException();
        if (req.result != UnityWebRequest.Result.Success)
            throw new ApiException((int)req.responseCode, req.error);
    }
}
```

### Data Transfer Objects

```csharp
// ─── Plays ──────────────────────────────────────────────────────────────────
[Serializable] public class PlaysResponse
{
    public string user_id;
    public int    order_plays_remaining;
    public int    ad_plays_remaining_today;
    public int    ad_plays_limit_per_day;
    public int    total_plays_remaining;
    public string plays_calculation;
}

// ─── Ad Status ──────────────────────────────────────────────────────────────
[Serializable] public class AdStatusResponse
{
    public bool   can_watch_ad;
    public int    ad_plays_granted_today;
    public int    ad_plays_remaining_today;
    public int    ad_plays_limit_per_day;
    public string resets_at;
}

// ─── Grant Ad Play ──────────────────────────────────────────────────────────
[Serializable] public class GrantAdPlayRequest
{
    public string ad_placement_id;
    public string ad_unit_id;
}

[Serializable] public class GrantAdPlayResponse
{
    public bool   granted;
    public int    ad_plays_remaining_today;
    public int    total_plays_remaining;
    public string resets_at;
}

// ─── Record Play ─────────────────────────────────────────────────────────────
[Serializable] public class RecordPlayRequest
{
    public string game_session_id;
    public string game_type;
    public bool   won;
    public string win_level;
}

[Serializable] public class RecordPlayResponse
{
    public string play_id;
    public int    plays_remaining;
    public bool   won;
    public string coupon_code;
    public string coupon_discount_type;
    public string coupon_discount_value;
    public string coupon_valid_until;
}

// ─── Rewards ─────────────────────────────────────────────────────────────────
[Serializable] public class RewardCoupon
{
    public string code;
    public string discount_type;
    public string discount_value;
    public string valid_until;
    public bool   is_used;
}

[Serializable] public class Reward
{
    public string       id;
    public string       game_type;
    public string       win_level;
    public string       reward_tier_name;
    public RewardCoupon coupon;
    public string       created_at;
}

// ─── Exceptions ──────────────────────────────────────────────────────────────
public class UnauthorizedException     : Exception { }
public class NoPlaysRemainingException : Exception { }
public class ApiException : Exception
{
    public int StatusCode;
    public ApiException(int code, string msg) : base(msg) { StatusCode = code; }
}

// ─── Array helper (Unity JsonUtility workaround) ─────────────────────────────
public static class JsonHelper
{
    public static T[] FromJsonArray<T>(string json)
    {
        string wrapped = $"{{\"array\":{json}}}";
        var wrapper = JsonUtility.FromJson<Wrapper<T>>(wrapped);
        return wrapper.array;
    }
    [Serializable] private class Wrapper<T> { public T[] array; }
}
```

---

## 8. Game Types and Win Levels

The `game_type` and `win_level` strings sent in `record-play` **must exactly match**
what the Dwarikas manager team has configured in the reward tier table.
A mismatch means the play is consumed but **no coupon is issued** (the play is wasted).

**Confirm these values with the backend/product team before implementing game logic.**

### Supported `game_type` values

| Value | Game |
|---|---|
| `spin_wheel` | Spin-the-wheel |
| `scratch_card` | Scratch card |
| `quiz` | Trivia / quiz challenge |

### Example `win_level` values

| `game_type` | `win_level` | Example prize |
|---|---|---|
| `spin_wheel` | `jackpot` | 20% off |
| `spin_wheel` | `silver` | 10% off |
| `spin_wheel` | `bronze` | 5% off |
| `scratch_card` | `jackpot` | ₹100 off |
| `quiz` | `perfect_score` | 15% off |
| `quiz` | `pass` | 5% off |

> **Wildcard:** The backend supports `win_level = "any"` as a fallback tier. If your
> `win_level` string doesn't match any exact tier, the backend falls back to `"any"`.

---

## 9. Error Handling Reference

| HTTP Code | Scenario | Recommended UI |
|---|---|---|
| `200` + `won: true` | Win — coupon issued | Show win screen with `coupon_code` |
| `200` + `won: false` | Loss — play consumed | Show loss screen + interstitial |
| `200` + `won: true` + `coupon_code: null` | Win but no reward tier configured | "You won — prize coming soon!" |
| `200` + `granted: true` | Ad play granted | "+1 Play!" animation, update count |
| `400` | Bad payload | Log internally, show generic error |
| `401` | JWT expired | Redirect to login screen immediately |
| `403` from `record-play` | No plays remaining | Show "No plays left" screen |
| `403` from `grant-ad-play` | Daily ad cap reached | "Come back tomorrow" with reset time |
| `5xx` / Network | Server error or offline | Show retry button (see Section 10) |

---

## 10. Network Retry and Idempotency

### `record-play/` — Safe to Retry

Generate the `game_session_id` **once, before the game runs**, and persist it:

```csharp
// Generate before the game starts
string sessionId = Guid.NewGuid().ToString();

// Persist immediately (crash safety)
PlayerPrefs.SetString("pending_session_id", sessionId);
PlayerPrefs.Save();

// After successful API response:
PlayerPrefs.DeleteKey("pending_session_id");
```

On network failure, retry with the **same** `game_session_id`. The backend's UNIQUE
constraint on `game_session_id` means it will return the original result without
double-deducting a play or double-issuing a coupon.

**On next app launch**, check for a pending session and complete it before showing the lobby:

```csharp
string pendingId = PlayerPrefs.GetString("pending_session_id", null);
if (!string.IsNullOrEmpty(pendingId))
    await CompletePendingPlayAsync(pendingId);
```

### `grant-ad-play/` — Not Retry-Safe

Do **not** retry `grant-ad-play/` on network failure — the daily cap is the safety
mechanism here. If the call fails, the player can simply watch another ad (they haven't
been charged a play).

---

## 11. Testing Against the Backend

### Test Accounts

The backend team will provide Supabase test accounts with pre-seeded completed orders
so test users have plays available from the start.

### Environments

| Environment | Base URL |
|---|---|
| Staging | *(to be provided by backend team)* |
| Production | `https://api.dwarikas.com` |

**Always use Staging for development and QA.**

### Simulating Edge Cases

| Scenario | How to test |
|---|---|
| No order plays | Use a fresh test account with no orders |
| Ad cap reached | Call `grant-ad-play/` 5 times — 6th should return 403 |
| Ad cap resets | Check `resets_at` field; verify count resets at midnight IST |
| Duplicate session | Call `record-play/` twice with same `game_session_id` — should return same result |
| JWT expiry | Wait 1 hour, then call any endpoint — should get 401 |
| No matching reward tier | Send `win_level: "nonexistent"` — `coupon_code` will be null |

### Manual Testing with curl

```bash
# Check plays and ad quota
curl -X GET "https://staging.api.dwarikas.com/api/v1/gaming/earn/" \
  -H "Authorization: Bearer <jwt>"

# Check ad status only
curl -X GET "https://staging.api.dwarikas.com/api/v1/gaming/ad-status/" \
  -H "Authorization: Bearer <jwt>"

# Grant ad play (simulate OnAdRewarded)
curl -X POST "https://staging.api.dwarikas.com/api/v1/gaming/grant-ad-play/" \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{ "ad_placement_id": "Rewarded_Android", "ad_unit_id": "test_unit" }'

# Record a win
curl -X POST "https://staging.api.dwarikas.com/api/v1/gaming/record-play/" \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "game_session_id": "test-session-001",
    "game_type": "spin_wheel",
    "won": true,
    "win_level": "jackpot"
  }'

# Record a loss
curl -X POST "https://staging.api.dwarikas.com/api/v1/gaming/record-play/" \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "game_session_id": "test-session-002",
    "game_type": "spin_wheel",
    "won": false
  }'
```

---

## 12. UI/UX Guidelines

### Lobby Screen — Required Elements

| Element | Logic |
|---|---|
| Play count | "You have **{total_plays_remaining}** plays" — show prominently |
| Play source breakdown | "({order_plays_remaining} from orders + {ad_plays_remaining_today} free today)" |
| **Play Now** button | Active if `total_plays_remaining > 0`; disabled (greyed) if 0 |
| **Watch Ad ▶** button | Active if `can_watch_ad = true`; greyed + "Resets at {time}" if false |
| Shop CTA | "Shop to earn more plays" with deep link to Dwarikas app — always visible |

### Watch Ad Button States

```
┌─────────────────────────────┐
│  ▶  Watch Ad — Earn a Play  │   ← Active state (can_watch_ad = true)
│     3 free plays left today │
└─────────────────────────────┘

┌─────────────────────────────┐
│  ▶  Watch Ad  (greyed out)  │   ← Capped state (can_watch_ad = false)
│  Free plays reset at 12:00  │
└─────────────────────────────┘
```

### Win Screen — Required Elements

- 🎉 Celebration animation / confetti (make it feel premium)
- Prize tier name (e.g., "Grand Prize!")
- Coupon code — large, easy to read, tap-to-copy: `GAME-A3XK9P`
- Discount summary: "20% off your next Dwarikas order"
- Validity: "Valid until 13 Jul 2026"
- WhatsApp confirmation: "We've also sent this to your WhatsApp"
- CTA button: **"Open Dwarikas App to Redeem"** — deep link to checkout
- **Do NOT show an interstitial ad on the win screen**

### Loss Screen — Required Elements

- Friendly message (e.g., "Almost! Try again?")
- Remaining plays: "You have {plays_remaining} plays left"
- If `plays_remaining == 0` and `can_watch_ad == true` → "Watch an ad for a free play!"
- Show the loss screen for **at least 2 seconds** before the interstitial fires
- After interstitial closes → return to lobby automatically

### Ad Flow UX

- Show a brief loading state while the rewarded ad loads ("Loading your reward…")
- If ad fails to load, show: "Ad not available right now. Try again in a moment."
- After watching a full ad, animate "+1 Play!" badge on the play counter before updating
- Never interrupt gameplay mid-spin/mid-scratch with an ad

### Reward History Screen

- List all items from `GET /gaming/rewards/`
- Show coupon code, discount type, game type, and date won
- Mark used coupons (`is_used: true`) as greyed/strikethrough
- CTA: "Open Dwarikas App to redeem" for each unused coupon

---

## Questions & Contact

Contact the Dwarikas backend team for:
- Supabase project URL and anon key
- LevelPlay App Key and Placement IDs
- Staging API base URL
- Test accounts with pre-seeded plays
- Configured `game_type` and `win_level` values for reward tiers
- Any changes to the API contract
