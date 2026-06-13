# Dwarikas Unity Game — Developer Integration Guide

**Audience:** Unity game developers building the Dwarikas loyalty game (Android / iOS)  
**Backend:** Dwarikas API (Django / Cloud Run)  
**Auth system:** Supabase (shared with the Dwarikas shopping app)  
**Last updated:** 2026-06-13

---

## Overview

The Dwarikas loyalty game is a **first-party Unity mobile game** (Android + iOS) that rewards
customers for shopping. Players earn game plays by making purchases in the Dwarikas app. When
they win a game (spin-the-wheel, scratch card, quiz, etc.), the backend automatically issues a
discount coupon to their Dwarikas account.

As a Unity developer, your responsibilities are:

1. **Authenticate** the player using the same Supabase project as the Dwarikas app.
2. **Check** how many game plays the player has remaining before showing the game.
3. **Run** the game and determine the outcome (win / loss) using Unity game logic.
4. **Report** the outcome to the Dwarikas backend via one API call.
5. **Display** the coupon code to the player if they won.

The backend handles coupon generation, WhatsApp notification, and reward storage.
Your game only needs to make **two API calls** in the normal flow.

---

## Table of Contents

1. [Project Credentials](#1-project-credentials)
2. [Unity SDK Setup](#2-unity-sdk-setup)
3. [Authentication Flow](#3-authentication-flow)
4. [API Reference](#4-api-reference)
5. [Complete Game Flow — Step by Step](#5-complete-game-flow--step-by-step)
6. [C# Code Samples](#6-c-code-samples)
7. [Game Types and Win Levels](#7-game-types-and-win-levels)
8. [Error Handling Reference](#8-error-handling-reference)
9. [Network Retry and Idempotency](#9-network-retry-and-idempotency)
10. [Testing Against the Backend](#10-testing-against-the-backend)
11. [UI/UX Guidelines](#11-uiux-guidelines)

---

## 1. Project Credentials

> **These values will be provided to you by the Dwarikas backend team before development starts.**
> Do NOT hardcode production credentials in source control.

| Credential | Description | Where to get it |
|---|---|---|
| `SUPABASE_URL` | Your Supabase project URL | Provided by backend team |
| `SUPABASE_ANON_KEY` | Public anon key for the Supabase project | Provided by backend team |
| `DWARIKAS_API_BASE_URL` | Base URL of the Dwarikas API | e.g., `https://api.dwarikas.com` |

Store these in a Unity `ScriptableObject` or `Resources` file that is excluded from source
control via `.gitignore`. **Never commit API keys to git.**

Example `GameConfig.cs`:
```csharp
[CreateAssetMenu(fileName = "GameConfig", menuName = "Dwarikas/Game Config")]
public class GameConfig : ScriptableObject
{
    public string SupabaseUrl;
    public string SupabaseAnonKey;
    public string DwarikasApiBaseUrl;
}
```

---

## 2. Unity SDK Setup

### Supabase C# SDK

Add the Supabase community SDK to your Unity project.

**Via Package Manager → Add by git URL:**
```
https://github.com/supabase-community/supabase-csharp.git
```

**Or via NuGet for Unity** (if using NuGetForUnity package):
```
Supabase
Supabase.Gotrue
```

**Minimum Unity version:** 2019.4 LTS  
**Supported platforms:** Android (API 24+), iOS (14+)

### Initialise the Client

Create a singleton `SupabaseManager` that initialises once at app start and is accessible globally.

```csharp
using Supabase;
using UnityEngine;

public class SupabaseManager : MonoBehaviour
{
    public static SupabaseManager Instance { get; private set; }
    public Supabase.Client Client { get; private set; }

    [SerializeField] private GameConfig config;

    async void Awake()
    {
        if (Instance != null) { Destroy(gameObject); return; }
        Instance = this;
        DontDestroyOnLoad(gameObject);

        var options = new SupabaseOptions
        {
            AutoRefreshToken = true,   // IMPORTANT: keeps the JWT alive
            PersistSession   = true,   // Saves session to PlayerPrefs across app launches
        };

        Client = new Supabase.Client(config.SupabaseUrl, config.SupabaseAnonKey, options);
        await Client.InitializeAsync();
    }

    public string AccessToken => Client.Auth.CurrentSession?.AccessToken;
    public bool IsLoggedIn    => Client.Auth.CurrentUser != null;
}
```

---

## 3. Authentication Flow

### Key Principle

The player uses the **same email and password** for the Unity game and the Dwarikas shopping app.
Both apps share one Supabase project. After login, Supabase issues a JWT that is accepted by all
Dwarikas backend endpoints.

### Login Screen

Your game must have a login screen with email + password fields. After successful login, store the
session (the SDK does this automatically with `PersistSession = true`) and proceed to the game
lobby.

```csharp
public async Task<bool> Login(string email, string password)
{
    try
    {
        var session = await SupabaseManager.Instance.Client.Auth.SignIn(email, password);
        return session?.AccessToken != null;
    }
    catch (Exception ex)
    {
        Debug.LogError($"Login failed: {ex.Message}");
        return false;
    }
}
```

### Token Refresh

The SDK auto-refreshes the JWT when `AutoRefreshToken = true`. You do not need to manage token
expiry manually. Always read the token fresh before making an API call:

```csharp
string jwt = SupabaseManager.Instance.AccessToken;
```

### Session Persistence

With `PersistSession = true`, the player will not need to log in every time they open the game.
On app launch, call `InitializeAsync()` (done in `Awake`) — the SDK restores the previous session
from `PlayerPrefs` automatically.

### Logout

```csharp
await SupabaseManager.Instance.Client.Auth.SignOut();
```

---

## 4. API Reference

**Base URL:** `{DWARIKAS_API_BASE_URL}/api/v1`

**Authentication:** Every request must include the Supabase JWT in the `Authorization` header:
```
Authorization: Bearer <supabase_access_token>
Content-Type: application/json
```

> All endpoints return HTTP **401** if the JWT is missing or expired.

---

### 4.1 — GET `/gaming/earn/`

**Purpose:** Check how many game plays the player has earned and how many they have left.

**Call when:** On the game lobby / main menu screen, before showing the "Play" button.

**Request:**
```
GET {BASE_URL}/api/v1/gaming/earn/
Authorization: Bearer <jwt>
```
*(No request body)*

**Success Response — HTTP 200:**
```json
{
    "user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "plays_earned": 12,
    "plays_used": 10,
    "plays_remaining": 2,
    "plays_calculation": "1 play per confirmed order"
}
```

| Field | Type | Description |
|---|---|---|
| `plays_earned` | int | Total plays earned from completed Dwarikas orders |
| `plays_used` | int | Plays already consumed (win or loss) |
| `plays_remaining` | int | Plays still available — use this to decide if "Play" is enabled |
| `plays_calculation` | string | Human-readable formula, display in UI if desired |

**UI logic:**
- If `plays_remaining > 0` → show "Play Now" button (active)
- If `plays_remaining == 0` → show "No plays left — shop to earn more" message

---

### 4.2 — POST `/gaming/record-play/`

**Purpose:** Tell the backend that the player just played, and report whether they won.

**Call when:** Immediately **after** the game outcome is determined (after the spin stops, after the scratch reveal, after the quiz answer, etc.). Do **not** call this before the game starts.

**Request:**
```
POST {BASE_URL}/api/v1/gaming/record-play/
Authorization: Bearer <jwt>
Content-Type: application/json
```

**Request Body:**
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
| `game_session_id` | string | ✅ Always | A GUID you generate in Unity before each play. Used for deduplication. See [Section 9](#9-network-retry-and-idempotency). |
| `game_type` | string | ✅ Always | The type of game being played. Must match a configured value. See [Section 7](#7-game-types-and-win-levels). |
| `won` | boolean | ✅ Always | `true` if the player won, `false` if they lost. |
| `win_level` | string | ✅ If `won=true` | The prize tier the player won. Must match a configured value. See [Section 7](#7-game-types-and-win-levels). Omit or set `null` if `won=false`. |

**Success Response — Win — HTTP 200:**
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

**Success Response — Loss — HTTP 200:**
```json
{
    "play_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "plays_remaining": 1,
    "won": false,
    "coupon_code": null
}
```

| Field | Type | Description |
|---|---|---|
| `play_id` | string (UUID) | The ID of this play record on the server |
| `plays_remaining` | int | Updated play count after this play |
| `won` | boolean | Server-confirmed win status |
| `coupon_code` | string \| null | The coupon code to show the player. `null` on a loss or if no matching reward tier was configured. |
| `coupon_discount_type` | string | `"percentage"` or `"flat_amount"` — present only on win |
| `coupon_discount_value` | string | Numeric discount value as a string — present only on win |
| `coupon_valid_until` | string | ISO 8601 date `YYYY-MM-DD` — present only on win |

**Error responses:**

| HTTP Code | Meaning | What to do |
|---|---|---|
| `400 Bad Request` | Invalid payload (missing fields, wrong types) | Log the error, show generic "Something went wrong" screen |
| `401 Unauthorized` | JWT missing or expired | Redirect player to login screen |
| `403 Forbidden` | No plays remaining | Show "No plays left" screen |

> **Important:** The play is consumed by this call whether or not the player won. A 200 response
> (even with `won: false`) means one play was deducted.

---

### 4.3 — GET `/gaming/rewards/`

**Purpose:** List all reward coupons the player has earned from the game.

**Call when:** Displaying the player's "My Rewards" or "Reward History" screen in the game.

**Request:**
```
GET {BASE_URL}/api/v1/gaming/rewards/
Authorization: Bearer <jwt>
```

**Success Response — HTTP 200:**
```json
[
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
        "whatsapp_delivered": null,
        "created_at": "2026-06-13T10:23:45Z"
    }
]
```

The player redeems coupons in the **Dwarikas shopping app at checkout** — not in the game itself.
The game's reward history screen is read-only.

---

### 4.4 — GET `/gaming/rewards/<id>/`

**Purpose:** Get details of a single reward.

**Request:**
```
GET {BASE_URL}/api/v1/gaming/rewards/{reward_uuid}/
Authorization: Bearer <jwt>
```

Response shape is identical to a single item in the list above.

---

## 5. Complete Game Flow — Step by Step

```
APP LAUNCH
    │
    ├─ Restore session from PlayerPrefs (auto via SDK)
    │
    ├─ Is player logged in?
    │   ├─ NO  → Show Login Screen → player enters email/password → supabase.Auth.SignIn()
    │   └─ YES → Go to Game Lobby
    │
GAME LOBBY
    │
    ├─ Call GET /gaming/earn/
    │
    ├─ plays_remaining > 0 ?
    │   ├─ NO  → Show "No plays left — shop on Dwarikas to earn more!" banner
    │   └─ YES → Show "You have N plays!" + "Play Now" button (enabled)
    │
PLAYER TAPS "PLAY NOW"
    │
    ├─ Generate game_session_id = System.Guid.NewGuid().ToString()
    ├─ Store game_session_id for use after the game
    │
    ├─ Run the game animation / logic (spin, scratch, quiz, etc.)
    │
    ├─ Determine outcome:
    │   ├─ WIN  → set won = true, determine win_level (e.g., "jackpot", "silver")
    │   └─ LOSS → set won = false, win_level = null
    │
REPORT OUTCOME TO BACKEND
    │
    ├─ Call POST /gaming/record-play/ with { game_session_id, game_type, won, win_level }
    │
    ├─ HTTP 200, won = true  → Show Win Screen with coupon_code
    │                          Show coupon details (discount, valid until)
    │                          Show "Check your WhatsApp!" message
    │                          Show "Go to Dwarikas app to redeem" CTA
    │
    ├─ HTTP 200, won = false → Show "Better luck next time!" screen
    │                          Show plays_remaining count
    │
    ├─ HTTP 403              → Show "You have no more plays" screen
    │                          (Edge case: plays exhausted between /earn/ and /record-play/)
    │
    └─ HTTP 4xx / Network    → Show "Something went wrong" + Retry button
                               (Retry is safe — game_session_id deduplication prevents double-spend)
```

---

## 6. C# Code Samples

### Helper: Dwarikas API Client

```csharp
using System;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Networking;

public class DwarikasApiClient
{
    private readonly string _baseUrl;
    private readonly Func<string> _getToken;

    public DwarikasApiClient(string baseUrl, Func<string> getToken)
    {
        _baseUrl  = baseUrl.TrimEnd('/');
        _getToken = getToken;
    }

    // ─── GET /gaming/earn/ ──────────────────────────────────────────────────

    public async Task<PlaysResponse> GetPlaysAsync()
    {
        var req = UnityWebRequest.Get($"{_baseUrl}/api/v1/gaming/earn/");
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");

        await req.SendWebRequest();

        if (req.responseCode == 401)
            throw new UnauthorizedException();

        if (req.result != UnityWebRequest.Result.Success)
            throw new ApiException((int)req.responseCode, req.error);

        return JsonUtility.FromJson<PlaysResponse>(req.downloadHandler.text);
    }

    // ─── POST /gaming/record-play/ ──────────────────────────────────────────

    public async Task<RecordPlayResponse> RecordPlayAsync(RecordPlayRequest payload)
    {
        string json = JsonUtility.ToJson(payload);
        byte[] body = Encoding.UTF8.GetBytes(json);

        var req = new UnityWebRequest($"{_baseUrl}/api/v1/gaming/record-play/", "POST");
        req.uploadHandler   = new UploadHandlerRaw(body);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");
        req.SetRequestHeader("Content-Type",  "application/json");

        await req.SendWebRequest();

        if (req.responseCode == 401) throw new UnauthorizedException();
        if (req.responseCode == 403) throw new NoPlaysRemainingException();

        if (req.result != UnityWebRequest.Result.Success)
            throw new ApiException((int)req.responseCode, req.error);

        return JsonUtility.FromJson<RecordPlayResponse>(req.downloadHandler.text);
    }

    // ─── GET /gaming/rewards/ ───────────────────────────────────────────────

    public async Task<RewardListResponse> GetRewardsAsync()
    {
        var req = UnityWebRequest.Get($"{_baseUrl}/api/v1/gaming/rewards/");
        req.SetRequestHeader("Authorization", $"Bearer {_getToken()}");

        await req.SendWebRequest();

        if (req.responseCode == 401) throw new UnauthorizedException();
        if (req.result != UnityWebRequest.Result.Success)
            throw new ApiException((int)req.responseCode, req.error);

        return JsonUtility.FromJson<RewardListResponse>(req.downloadHandler.text);
    }
}
```

### Data Transfer Objects (C#)

```csharp
// ─── Request / Response models ──────────────────────────────────────────────

[Serializable]
public class PlaysResponse
{
    public string user_id;
    public int    plays_earned;
    public int    plays_used;
    public int    plays_remaining;
    public string plays_calculation;
}

[Serializable]
public class RecordPlayRequest
{
    public string game_session_id;
    public string game_type;
    public bool   won;
    public string win_level;   // null if lost
}

[Serializable]
public class RecordPlayResponse
{
    public string play_id;
    public int    plays_remaining;
    public bool   won;
    public string coupon_code;              // null if lost
    public string coupon_discount_type;     // null if lost
    public string coupon_discount_value;    // null if lost
    public string coupon_valid_until;       // null if lost
}

[Serializable]
public class RewardCoupon
{
    public string code;
    public string discount_type;
    public string discount_value;
    public string valid_until;
    public bool   is_used;
}

[Serializable]
public class Reward
{
    public string       id;
    public string       game_type;
    public string       win_level;
    public string       reward_tier_name;
    public RewardCoupon coupon;
    public string       created_at;
}

[Serializable]
public class RewardListResponse
{
    public Reward[] rewards;  // parse with a wrapper if needed
}

// ─── Custom exceptions ───────────────────────────────────────────────────────

public class UnauthorizedException    : Exception { }
public class NoPlaysRemainingException : Exception { }
public class ApiException : Exception
{
    public int StatusCode { get; }
    public ApiException(int code, string msg) : base(msg) { StatusCode = code; }
}
```

### Game Manager: Full Play Loop

```csharp
public class GameManager : MonoBehaviour
{
    [SerializeField] private GameConfig  config;
    [SerializeField] private LobbyScreen lobbyScreen;
    [SerializeField] private WinScreen   winScreen;
    [SerializeField] private LossScreen  lossScreen;

    private DwarikasApiClient _api;

    void Start()
    {
        _api = new DwarikasApiClient(
            config.DwarikasApiBaseUrl,
            () => SupabaseManager.Instance.AccessToken
        );
        _ = LoadLobbyAsync();
    }

    // ── Step 1: Load lobby ───────────────────────────────────────────────────

    async Task LoadLobbyAsync()
    {
        try
        {
            var plays = await _api.GetPlaysAsync();
            lobbyScreen.Show(plays.plays_remaining, plays.plays_earned);
        }
        catch (UnauthorizedException)
        {
            SceneManager.LoadScene("LoginScene");
        }
        catch (Exception ex)
        {
            Debug.LogError($"Failed to load plays: {ex.Message}");
            lobbyScreen.ShowError("Could not load your plays. Check your connection.");
        }
    }

    // ── Step 2: Player taps Play ─────────────────────────────────────────────

    public async Task OnPlayButtonPressed(string gameType)
    {
        // Generate session ID BEFORE the game runs
        string sessionId = Guid.NewGuid().ToString();

        // Run the game — returns win outcome
        var outcome = await RunGameAsync(gameType);

        // Report to backend
        await ReportOutcomeAsync(sessionId, gameType, outcome.Won, outcome.WinLevel);
    }

    async Task ReportOutcomeAsync(string sessionId, string gameType, bool won, string winLevel)
    {
        var payload = new RecordPlayRequest
        {
            game_session_id = sessionId,
            game_type       = gameType,
            won             = won,
            win_level       = won ? winLevel : null,
        };

        try
        {
            var result = await _api.RecordPlayAsync(payload);

            if (result.won)
                winScreen.Show(result.coupon_code, result.coupon_discount_value,
                               result.coupon_discount_type, result.coupon_valid_until,
                               result.plays_remaining);
            else
                lossScreen.Show(result.plays_remaining);
        }
        catch (NoPlaysRemainingException)
        {
            lossScreen.ShowNoPlays();
        }
        catch (UnauthorizedException)
        {
            SceneManager.LoadScene("LoginScene");
        }
        catch (Exception ex)
        {
            Debug.LogError($"record-play failed: {ex.Message}");
            // Show retry — see Section 9 for retry safety
            ShowRetryDialog(payload);
        }
    }

    async Task<GameOutcome> RunGameAsync(string gameType)
    {
        // ───────────────────────────────────────────────────────────────────
        // YOUR GAME LOGIC HERE
        // Run the spin animation, scratch reveal, quiz question, etc.
        // Determine the outcome on the client side.
        // Return the win/loss result.
        // ───────────────────────────────────────────────────────────────────
        throw new NotImplementedException("Implement game logic here.");
    }
}

public class GameOutcome
{
    public bool   Won;
    public string WinLevel;  // e.g., "jackpot", "silver", "bronze" — null if lost
}
```

---

## 7. Game Types and Win Levels

The `game_type` and `win_level` strings you send in `record-play` **must match** what the
Dwarikas manager team has configured in the reward tier table. Mismatched strings result in
`won=true` being recorded (the play is consumed) but **no coupon being issued** — so get
these values right.

**Coordinate with the backend / product team** before development. As a reference, expected
values are:

### Supported `game_type` values

| Value | Game |
|---|---|
| `spin_wheel` | Spin-the-wheel |
| `scratch_card` | Scratch card |
| `quiz` | Trivia / quiz challenge |

### Example `win_level` values per game type

| `game_type` | `win_level` | Example prize |
|---|---|---|
| `spin_wheel` | `jackpot` | 20% off |
| `spin_wheel` | `silver` | 10% off |
| `spin_wheel` | `bronze` | 5% off |
| `scratch_card` | `jackpot` | ₹100 off |
| `scratch_card` | `any` | Wildcard — matches any unrecognised win level |
| `quiz` | `perfect_score` | 15% off |
| `quiz` | `pass` | 5% off |

> The backend supports an `any` wildcard for `win_level`. If no exact match is found, the backend
> falls back to the `any` tier for that `game_type`. Coordinate with the product team on which
> tiers should be configured.

---

## 8. Error Handling Reference

| HTTP Code | Scenario | Recommended UI |
|---|---|---|
| `200` + `won: true` | Win — coupon issued | Show win screen with `coupon_code` |
| `200` + `won: false` | Loss — play consumed | Show loss screen with plays remaining |
| `200` + `won: true` + `coupon_code: null` | Win but no reward tier configured | Show "You won but no prize is configured yet — please contact support" |
| `400` | Bad request payload | Log internally. Show generic error. Do not retry with same payload. |
| `401` | JWT expired or missing | Redirect to login screen |
| `403` | No plays remaining | Show "No plays left" screen — don't retry |
| `5xx` / Network failure | Server error or no connection | Show retry button — safe to retry with same `game_session_id` |

---

## 9. Network Retry and Idempotency

The `game_session_id` field is the key safety mechanism for retries.

**Generate the session ID once, before each play:**
```csharp
// Generate BEFORE running the game, store it
string sessionId = Guid.NewGuid().ToString();
```

**On network failure, retry with the same `game_session_id`:**
```csharp
// Safe to call multiple times with the same sessionId
// The backend will return the same result and will NOT double-deduct a play
await _api.RecordPlayAsync(new RecordPlayRequest {
    game_session_id = sessionId,   // same ID as before
    game_type       = gameType,
    won             = won,
    win_level       = winLevel,
});
```

The backend stores `game_session_id` with a UNIQUE constraint. If it receives the same ID twice,
it returns the original result without creating a new record. This means:
- ✅ Retry after network timeout is always safe
- ✅ App crash and restart is safe
- ✅ Player cannot spin twice by force-quitting the app

**Persist the session ID in PlayerPrefs before calling the API**, so it survives app crashes:
```csharp
PlayerPrefs.SetString("pending_game_session_id", sessionId);
PlayerPrefs.Save();

// After successful API response, clear it
PlayerPrefs.DeleteKey("pending_game_session_id");
```

On next app launch, check if a `pending_game_session_id` exists and complete the pending call
before showing the lobby.

---

## 10. Testing Against the Backend

### Test Accounts

The backend team will provide test Supabase accounts with pre-seeded completed orders so the test
users have plays available. Use these during development — do not use real customer accounts.

### Staging Environment

| Environment | Base URL |
|---|---|
| Staging | `https://staging-api.dwarikas.com` *(to be provided)* |
| Production | `https://api.dwarikas.com` *(to be provided)* |

Use Staging for all development and QA testing. Never test with production credentials.

### Simulating Edge Cases

| Scenario | How to test |
|---|---|
| No plays remaining | Use a test account that has consumed all plays, or call `/gaming/record-play/` until `plays_remaining` hits 0 |
| Duplicate session ID | Call `/gaming/record-play/` twice with the same `game_session_id` — second call should return the original result |
| JWT expiry | Wait for token to expire (1 hour by default), then call any endpoint — should get 401 |
| Win with no matching tier | Send a `win_level` not configured by the manager team — response will have `won: true` but `coupon_code: null` |

### Manual API Testing (curl / Postman)

```bash
# 1. Get plays
curl -X GET https://staging-api.dwarikas.com/api/v1/gaming/earn/ \
  -H "Authorization: Bearer <your_jwt>"

# 2. Record a win
curl -X POST https://staging-api.dwarikas.com/api/v1/gaming/record-play/ \
  -H "Authorization: Bearer <your_jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "game_session_id": "test-session-001",
    "game_type": "spin_wheel",
    "won": true,
    "win_level": "jackpot"
  }'

# 3. Record a loss
curl -X POST https://staging-api.dwarikas.com/api/v1/gaming/record-play/ \
  -H "Authorization: Bearer <your_jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "game_session_id": "test-session-002",
    "game_type": "spin_wheel",
    "won": false
  }'

# 4. List all rewards
curl -X GET https://staging-api.dwarikas.com/api/v1/gaming/rewards/ \
  -H "Authorization: Bearer <your_jwt>"
```

---

## 11. UI/UX Guidelines

### Win Screen — Required Elements

When `won=true` in the response, show:

- 🎉 Celebration animation / confetti
- Prize name (from `reward_tier_name` if you call `/gaming/rewards/`) or generic "You Won!"
- **Coupon code** — displayed prominently, easy to copy (`coupon_code`)
- Discount summary: e.g., "20% off your next order" (`coupon_discount_value` + `coupon_discount_type`)
- Validity: "Valid until 13 Jul 2026" (`coupon_valid_until`)
- WhatsApp message: "We've also sent this code to your WhatsApp"
- CTA button: **"Open Dwarikas App to Redeem"** — this should deep-link to the Dwarikas shopping app

### Loss Screen — Required Elements

- A friendly message — avoid negative language (e.g., "Almost!" or "Keep trying!")
- Remaining plays count: "You have N plays left"
- If plays = 0: "Shop on Dwarikas to earn more plays" — with a deep link to the shopping app

### Lobby Screen — Required Elements

- Player's name / avatar (from Supabase user profile)
- Current play count: "You have N plays"
- If plays = 0: Disable "Play Now" button and show earn message
- "My Rewards" button → navigate to reward history screen

### Reward History Screen

- List all items from `GET /gaming/rewards/`
- Show coupon code, discount, game type, date won
- Show whether coupon is `is_used: true` (greyed out) or available (highlighted)
- Remind player to redeem in the Dwarikas app

---

## Questions?

Contact the Dwarikas backend team for:
- Supabase credentials and project URL
- Staging API base URL
- Configured `game_type` and `win_level` values for your reward tiers
- Test accounts with pre-seeded plays
- Any changes to the API contract
