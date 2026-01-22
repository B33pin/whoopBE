"""
WHOOP Backend Service - Main Application

FastAPI backend for WHOOP OAuth integration.
Handles OAuth flow, token storage, and data fetching for Nutrogen mobile app.

Swagger UI: /docs
ReDoc: /redoc
"""

import os
import secrets
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional, List
from urllib.parse import urlencode
from enum import Enum

import httpx
import pytz
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

# Load environment variables
load_dotenv()

# Configuration
WHOOP_CLIENT_ID = os.getenv("WHOOP_CLIENT_ID", "")
WHOOP_CLIENT_SECRET = os.getenv("WHOOP_CLIENT_SECRET", "")
WHOOP_REDIRECT_URI = os.getenv("WHOOP_REDIRECT_URI", "")
APP_REDIRECT_URI = os.getenv("APP_REDIRECT_URI", "nutrogen://whoop/callback")
WHOOP_API_BASE_URL = os.getenv("WHOOP_API_BASE_URL", "https://api.prod.whoop.com")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============== Timezone Configuration ==============

# Popular timezones for dropdown
POPULAR_TIMEZONES = {
    # North America
    "US/Pacific": "🇺🇸 US Pacific (Los Angeles)",
    "US/Mountain": "🇺🇸 US Mountain (Denver)",
    "US/Central": "🇺🇸 US Central (Chicago)",
    "US/Eastern": "🇺🇸 US Eastern (New York)",
    "Canada/Pacific": "🇨🇦 Canada Pacific (Vancouver)",
    "Canada/Central": "🇨🇦 Canada Central (Winnipeg)",
    "Canada/Eastern": "🇨🇦 Canada Eastern (Toronto)",
    # Europe
    "Europe/London": "🇬🇧 UK (London)",
    "Europe/Paris": "🇫🇷 France (Paris)",
    "Europe/Berlin": "🇩🇪 Germany (Berlin)",
    "Europe/Amsterdam": "🇳🇱 Netherlands (Amsterdam)",
    "Europe/Madrid": "🇪🇸 Spain (Madrid)",
    "Europe/Rome": "🇮🇹 Italy (Rome)",
    # Asia Pacific
    "Asia/Dubai": "🇦🇪 UAE (Dubai)",
    "Asia/Kolkata": "🇮🇳 India (Mumbai/Delhi)",
    "Asia/Singapore": "🇸🇬 Singapore",
    "Asia/Hong_Kong": "🇭🇰 Hong Kong",
    "Asia/Tokyo": "🇯🇵 Japan (Tokyo)",
    "Asia/Seoul": "🇰🇷 South Korea (Seoul)",
    "Asia/Shanghai": "🇨🇳 China (Shanghai)",
    # Australia & New Zealand
    "Australia/Perth": "🇦🇺 Australia Western (Perth)",
    "Australia/Adelaide": "🇦🇺 Australia Central (Adelaide)",
    "Australia/Sydney": "🇦🇺 Australia Eastern (Sydney)",
    "Australia/Brisbane": "🇦🇺 Australia Queensland (Brisbane)",
    "Australia/Melbourne": "🇦🇺 Australia (Melbourne)",
    "Pacific/Auckland": "🇳🇿 New Zealand (Auckland)",
    # South America
    "America/Sao_Paulo": "🇧🇷 Brazil (São Paulo)",
    "America/Argentina/Buenos_Aires": "🇦🇷 Argentina (Buenos Aires)",
    # Africa
    "Africa/Johannesburg": "🇿🇦 South Africa (Johannesburg)",
    "Africa/Cairo": "🇪🇬 Egypt (Cairo)",
    # UTC
    "UTC": "🌐 UTC (No timezone)",
}

class TimezoneEnum(str, Enum):
    """Popular timezones for API dropdown."""
    # North America
    US_PACIFIC = "US/Pacific"
    US_MOUNTAIN = "US/Mountain"
    US_CENTRAL = "US/Central"
    US_EASTERN = "US/Eastern"
    CANADA_PACIFIC = "Canada/Pacific"
    CANADA_CENTRAL = "Canada/Central"
    CANADA_EASTERN = "Canada/Eastern"
    # Europe
    EUROPE_LONDON = "Europe/London"
    EUROPE_PARIS = "Europe/Paris"
    EUROPE_BERLIN = "Europe/Berlin"
    EUROPE_AMSTERDAM = "Europe/Amsterdam"
    EUROPE_MADRID = "Europe/Madrid"
    EUROPE_ROME = "Europe/Rome"
    # Asia Pacific
    ASIA_DUBAI = "Asia/Dubai"
    ASIA_KOLKATA = "Asia/Kolkata"
    ASIA_SINGAPORE = "Asia/Singapore"
    ASIA_HONG_KONG = "Asia/Hong_Kong"
    ASIA_TOKYO = "Asia/Tokyo"
    ASIA_SEOUL = "Asia/Seoul"
    ASIA_SHANGHAI = "Asia/Shanghai"
    # Australia & New Zealand
    AUSTRALIA_PERTH = "Australia/Perth"
    AUSTRALIA_ADELAIDE = "Australia/Adelaide"
    AUSTRALIA_SYDNEY = "Australia/Sydney"
    AUSTRALIA_BRISBANE = "Australia/Brisbane"
    AUSTRALIA_MELBOURNE = "Australia/Melbourne"
    PACIFIC_AUCKLAND = "Pacific/Auckland"
    # South America
    AMERICA_SAO_PAULO = "America/Sao_Paulo"
    AMERICA_BUENOS_AIRES = "America/Argentina/Buenos_Aires"
    # Africa
    AFRICA_JOHANNESBURG = "Africa/Johannesburg"
    AFRICA_CAIRO = "Africa/Cairo"
    # UTC
    UTC = "UTC"


def convert_local_date_to_utc(date_str: str, tz_name: str, end_of_day: bool = False) -> str:
    """
    Convert a local date string (YYYY-MM-DD) to UTC ISO8601 timestamp.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        tz_name: Timezone name (e.g., "Australia/Sydney")
        end_of_day: If True, returns 23:59:59 of that day, else 00:00:00
    
    Returns:
        UTC timestamp in ISO8601 format (e.g., "2026-01-22T13:00:00.000Z")
    """
    try:
        tz = pytz.timezone(tz_name)
        # Parse the date
        local_date = datetime.strptime(date_str, "%Y-%m-%d")
        
        if end_of_day:
            local_date = local_date.replace(hour=23, minute=59, second=59)
        else:
            local_date = local_date.replace(hour=0, minute=0, second=0)
        
        # Localize to the timezone
        local_dt = tz.localize(local_date)
        # Convert to UTC
        utc_dt = local_dt.astimezone(pytz.UTC)
        
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception as e:
        logger.error(f"Timezone conversion error: {e}")
        # Fallback: return date as-is with time
        if end_of_day:
            return f"{date_str}T23:59:59.000Z"
        return f"{date_str}T00:00:00.000Z"


def get_date_range_for_days(days: int, tz_name: str = "UTC") -> tuple[str, str]:
    """
    Get UTC start/end timestamps for the last N days in user's timezone.
    
    Args:
        days: Number of days to look back
        tz_name: User's timezone
    
    Returns:
        Tuple of (start_utc, end_utc) in ISO8601 format
    """
    try:
        tz = pytz.timezone(tz_name)
        # Get current time in user's timezone
        now_local = datetime.now(tz)
        # End is now
        end_utc = now_local.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        # Start is N days ago at midnight local time
        start_local = (now_local - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_local.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        
        return start_utc, end_utc
    except Exception as e:
        logger.error(f"Date range calculation error: {e}")
        # Fallback to UTC
        end = datetime.now(dt_timezone.utc)
        start = end - timedelta(days=days)
        return start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), end.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# In-memory storage (use Redis/DB in production!)
# Format: {user_id: {access_token, refresh_token, expires_at, whoop_user_id}}
token_store: dict[str, dict] = {}
# Format: {state: user_id}
state_store: dict[str, str] = {}

# ============== Pydantic Models for Swagger ==============

class AuthUrlRequest(BaseModel):
    user_id: str = Field(..., description="Your app's user ID", example="user_123")

class AuthUrlResponse(BaseModel):
    authorization_url: str = Field(..., description="URL to redirect user to WHOOP OAuth")
    state: str = Field(..., description="State token for CSRF protection")

class CallbackRequest(BaseModel):
    code: str = Field(..., description="Authorization code from WHOOP")
    state: str = Field(..., description="State token to verify")

class CallbackResponse(BaseModel):
    success: bool
    whoop_user_id: Optional[str] = None
    connected_at: Optional[str] = None
    error: Optional[str] = None

class DisconnectResponse(BaseModel):
    success: bool

class ConnectionStatus(BaseModel):
    connected: bool
    whoop_user_id: Optional[str] = None
    expires_at: Optional[str] = None

class UserProfile(BaseModel):
    user_id: int
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class RecoveryScore(BaseModel):
    recovery_score: Optional[float] = Field(None, description="Recovery score 0-100%")
    resting_heart_rate: Optional[float] = Field(None, description="Resting heart rate in BPM")
    hrv_rmssd_milli: Optional[float] = Field(None, description="HRV in milliseconds")
    spo2_percentage: Optional[float] = None
    skin_temp_celsius: Optional[float] = None

class Recovery(BaseModel):
    cycle_id: str
    sleep_id: str
    user_id: int
    created_at: str
    updated_at: Optional[str] = None
    score: Optional[RecoveryScore] = None

class SleepStageSummary(BaseModel):
    total_in_bed_time_milli: Optional[int] = None
    total_awake_time_milli: Optional[int] = None
    total_light_sleep_time_milli: Optional[int] = None
    total_slow_wave_sleep_time_milli: Optional[int] = None
    total_rem_sleep_time_milli: Optional[int] = None

class SleepScore(BaseModel):
    stage_summary: Optional[SleepStageSummary] = None
    respiratory_rate: Optional[float] = None
    sleep_performance_percentage: Optional[float] = None
    sleep_efficiency_percentage: Optional[float] = None

class Sleep(BaseModel):
    id: str
    user_id: int
    created_at: str
    start: str
    end: str
    nap: bool = Field(False, description="Whether this was a nap")
    score: Optional[SleepScore] = None

class WorkoutScore(BaseModel):
    strain: Optional[float] = Field(None, description="Strain score 0-21")
    average_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None
    kilojoule: Optional[float] = None
    distance_meter: Optional[float] = None

class Workout(BaseModel):
    id: str
    user_id: int
    created_at: str
    start: str
    end: str
    sport_id: int
    score: Optional[WorkoutScore] = None

class Cycle(BaseModel):
    id: str
    user_id: int
    created_at: str
    start: str
    end: Optional[str] = None
    days: Optional[int] = None

class WhoopDataResponse(BaseModel):
    recovery: List[dict] = Field(default=[], description="Recovery records")
    sleep: List[dict] = Field(default=[], description="Sleep records")
    workouts: List[dict] = Field(default=[], description="Workout records")
    cycles: List[dict] = Field(default=[], description="Cycle records")
    profile: Optional[dict] = Field(None, description="User profile")
    body_measurement: Optional[dict] = Field(None, description="Body measurements")

class HealthResponse(BaseModel):
    status: str
    service: str
    connected_users: int

# ============== FastAPI App with Swagger Config ==============

app = FastAPI(
    title="WHOOP Backend Service",
    description="""
## WHOOP Cloud API Integration Backend

This service handles OAuth authentication and data fetching from WHOOP's API.

### Features:
- 🔐 **OAuth 2.0 Authentication** - Secure connection to WHOOP accounts
- 📊 **Recovery Data** - Daily recovery scores, HRV, resting heart rate
- 😴 **Sleep Data** - Sleep stages, duration, efficiency
- 🏋️ **Workout Data** - Strain, heart rate, calories burned
- 🔄 **Cycle Data** - Physiological cycles

### Flow:
1. Call `/api/v1/whoop/auth-url` to get OAuth URL
2. User logs into WHOOP and authorizes
3. WHOOP redirects to callback with auth code
4. Call `/api/v1/whoop/data/{user_id}` to fetch data

### WHOOP API Scopes:
- `read:recovery` - Recovery scores
- `read:sleep` - Sleep data
- `read:workout` - Workout data
- `read:cycles` - Cycle data
- `read:profile` - User profile
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Health", "description": "Service health checks"},
        {"name": "Utilities", "description": "Timezone and utility endpoints"},
        {"name": "Authentication", "description": "OAuth authentication endpoints"},
        {"name": "Data", "description": "WHOOP data fetching endpoints"},
        {"name": "User", "description": "User profile and body data"},
    ]
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== Helper Functions ==============

def generate_state() -> str:
    """Generate a secure random state string."""
    return secrets.token_urlsafe(32)


async def refresh_token_if_needed(user_id: str) -> Optional[str]:
    """
    Refresh the access token if expired.
    
    IMPORTANT: WHOOP uses Refresh Token Rotation. When we refresh,
    WHOOP invalidates the old refresh token and sends a new one.
    We MUST save the new refresh token or the user will be logged out.
    """
    if user_id not in token_store:
        logger.warning(f"[Token Refresh] No token found for user {user_id}")
        return None
    
    token_data = token_store[user_id]
    expires_at = token_data.get("expires_at")
    
    # Check if token is expired (with 5 min buffer)
    if expires_at and datetime.fromisoformat(expires_at) > datetime.utcnow() + timedelta(minutes=5):
        return token_data["access_token"]
    
    # Token expired, need to refresh
    logger.info(f"[Token Refresh] Token expired for user {user_id}, refreshing...")
    
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        logger.error(f"[Token Refresh] No refresh token for user {user_id} - user must re-authenticate")
        # Remove invalid token data
        del token_store[user_id]
        return None
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{WHOOP_API_BASE_URL}/oauth/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET,
            },
        )
        
        if response.status_code != 200:
            logger.error(f"[Token Refresh] Failed for user {user_id}: {response.text}")
            # Token is invalid, remove it so user can re-authenticate
            del token_store[user_id]
            return None
        
        data = response.json()
        expires_in = data.get("expires_in", 3600)
        
        # CRITICAL: Save the NEW refresh token (Token Rotation)
        new_refresh_token = data.get("refresh_token")
        if new_refresh_token:
            logger.info(f"[Token Refresh] Got new refresh token for user {user_id}")
        else:
            logger.warning(f"[Token Refresh] No new refresh token in response for user {user_id}, keeping old one")
            new_refresh_token = refresh_token
        
        token_store[user_id] = {
            "access_token": data["access_token"],
            "refresh_token": new_refresh_token,
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat(),
            "whoop_user_id": token_data.get("whoop_user_id"),
        }
        
        logger.info(f"[Token Refresh] Successfully refreshed token for user {user_id}, expires in {expires_in}s")
        
        return data["access_token"]


# ============== Health Endpoints ==============

@app.get("/health", tags=["Health"], response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    
    Returns service status and number of connected users.
    """
    return HealthResponse(
        status="healthy",
        service="whoop-backend",
        connected_users=len(token_store)
    )


@app.get("/api/v1/whoop/connected-users", tags=["Health"])
async def list_connected_users():
    """
    List all connected user IDs (for debugging).
    
    ⚠️ Remove this endpoint in production!
    """
    return {
        "count": len(token_store),
        "user_ids": list(token_store.keys())
    }


# ============== Timezone Endpoints ==============

@app.get("/api/v1/timezones", tags=["Utilities"])
async def get_timezones():
    """
    Get list of supported timezones for dropdown.
    
    Returns popular timezones with friendly display names.
    Use the `id` value when calling data endpoints.
    
    **Example Response:**
    ```json
    {
      "timezones": [
        {"id": "Australia/Sydney", "name": "🇦🇺 Australia Eastern (Sydney)", "offset": "+11:00"},
        {"id": "US/Eastern", "name": "🇺🇸 US Eastern (New York)", "offset": "-05:00"}
      ]
    }
    ```
    """
    result = []
    for tz_id, tz_name in POPULAR_TIMEZONES.items():
        try:
            tz = pytz.timezone(tz_id)
            now = datetime.now(tz)
            offset = now.strftime("%z")
            # Format offset as +HH:MM
            offset_formatted = f"{offset[:3]}:{offset[3:]}"
            result.append({
                "id": tz_id,
                "name": tz_name,
                "offset": offset_formatted
            })
        except Exception:
            result.append({
                "id": tz_id,
                "name": tz_name,
                "offset": "?"
            })
    
    # Sort by offset
    result.sort(key=lambda x: x.get("offset", ""))
    
    return {"timezones": result}


@app.get("/api/v1/timezone/convert", tags=["Utilities"])
async def convert_date_to_utc(
    date: str = Query(..., description="Local date in YYYY-MM-DD format", example="2026-01-22"),
    timezone: TimezoneEnum = Query(..., description="User's timezone"),
):
    """
    Convert a local date to UTC timestamps.
    
    Useful for understanding how dates are converted before calling data endpoints.
    
    **Example:**
    - Input: `date=2026-01-22`, `timezone=Australia/Sydney`
    - Output: Shows that Jan 22 in Sydney = Jan 21 13:00 UTC to Jan 22 13:00 UTC
    """
    start_utc = convert_local_date_to_utc(date, timezone.value, end_of_day=False)
    end_utc = convert_local_date_to_utc(date, timezone.value, end_of_day=True)
    
    tz = pytz.timezone(timezone.value)
    offset = datetime.now(tz).strftime("%z")
    offset_formatted = f"{offset[:3]}:{offset[3:]}"
    
    return {
        "local_date": date,
        "timezone": timezone.value,
        "utc_offset": offset_formatted,
        "utc_start": start_utc,
        "utc_end": end_utc,
        "explanation": f"In {timezone.value}, {date} 00:00 = {start_utc} and {date} 23:59 = {end_utc}"
    }


# ============== Authentication Endpoints ==============

@app.post("/api/v1/whoop/auth-url", response_model=AuthUrlResponse, tags=["Authentication"])
async def get_auth_url(request: AuthUrlRequest):
    """
    Generate WHOOP OAuth authorization URL.
    
    The mobile app calls this to get the URL to open in a WebView or browser.
    After the user authorizes, WHOOP will redirect to the callback URL.
    
    **Request:**
    - `user_id`: Your app's internal user ID
    
    **Response:**
    - `authorization_url`: URL to redirect user to
    - `state`: State token for CSRF protection
    """
    if not WHOOP_CLIENT_ID:
        raise HTTPException(status_code=500, detail="WHOOP_CLIENT_ID not configured")
    
    state = generate_state()
    state_store[state] = request.user_id
    
    # WHOOP OAuth scopes - MUST include 'offline' for refresh tokens
    scopes = "read:recovery read:sleep read:workout read:cycles read:profile read:body_measurement offline"
    
    params = {
        "client_id": WHOOP_CLIENT_ID,
        "redirect_uri": WHOOP_REDIRECT_URI,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }
    
    authorization_url = f"{WHOOP_API_BASE_URL}/oauth/oauth2/auth?{urlencode(params)}"
    
    logger.info(f"Generated auth URL for user {request.user_id}")
    
    return AuthUrlResponse(authorization_url=authorization_url, state=state)


@app.get("/api/v1/whoop/callback", tags=["Authentication"])
async def oauth_callback_redirect(
    code: str = Query(..., description="Authorization code from WHOOP"),
    state: str = Query(..., description="State token to verify"),
):
    """
    OAuth callback endpoint (GET - browser redirect).
    
    WHOOP redirects here after user authorizes. This endpoint:
    1. Exchanges the code for access/refresh tokens
    2. Fetches the user's WHOOP profile
    3. Stores tokens securely
    4. Redirects to mobile app with success/error
    """
    if state not in state_store:
        # Redirect to app with error
        error_params = urlencode({"success": "false", "error": "invalid_state"})
        return RedirectResponse(f"{APP_REDIRECT_URI}?{error_params}")
    
    user_id = state_store.pop(state)
    
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{WHOOP_API_BASE_URL}/oauth/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET,
                "redirect_uri": WHOOP_REDIRECT_URI,
            },
        )
        
        if response.status_code != 200:
            logger.error(f"Token exchange failed: {response.text}")
            error_params = urlencode({"success": "false", "error": "token_exchange_failed"})
            return RedirectResponse(f"{APP_REDIRECT_URI}?{error_params}")
        
        data = response.json()
        expires_in = data.get("expires_in", 3600)
        refresh_token = data.get("refresh_token")
        
        # Log if we got a refresh token (requires 'offline' scope)
        if refresh_token:
            logger.info(f"[OAuth Callback GET] Got refresh token for user {user_id} - offline scope working!")
        else:
            logger.warning(f"[OAuth Callback GET] NO refresh token for user {user_id} - check if 'offline' scope is included!")
        
        # Get WHOOP user profile
        profile_response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/user/profile/basic",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        
        whoop_user_id = None
        if profile_response.status_code == 200:
            profile = profile_response.json()
            whoop_user_id = str(profile.get("user_id", ""))
        
        # Store tokens
        token_store[user_id] = {
            "access_token": data["access_token"],
            "refresh_token": refresh_token,
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat(),
            "whoop_user_id": whoop_user_id,
        }
        
        logger.info(f"Successfully connected WHOOP for user {user_id}, has_refresh_token={refresh_token is not None}")
        
        # Redirect to mobile app with success
        success_params = urlencode({
            "success": "true",
            "user_id": user_id,
            "whoop_user_id": whoop_user_id or "",
        })
        return RedirectResponse(f"{APP_REDIRECT_URI}?{success_params}")


@app.post("/api/v1/whoop/callback", response_model=CallbackResponse, tags=["Authentication"])
async def oauth_callback_manual(request: CallbackRequest):
    """
    Manual OAuth callback (POST - for app handling).
    
    Alternative to GET callback - mobile app can call this directly
    if handling the deep link parsing itself.
    """
    if request.state not in state_store:
        return CallbackResponse(success=False, error="invalid_state")
    
    user_id = state_store.pop(request.state)
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{WHOOP_API_BASE_URL}/oauth/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": request.code,
                "client_id": WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET,
                "redirect_uri": WHOOP_REDIRECT_URI,
            },
        )
        
        if response.status_code != 200:
            logger.error(f"Token exchange failed: {response.text}")
            return CallbackResponse(success=False, error="token_exchange_failed")
        
        data = response.json()
        expires_in = data.get("expires_in", 3600)
        refresh_token = data.get("refresh_token")
        
        # Log if we got a refresh token (requires 'offline' scope)
        if refresh_token:
            logger.info(f"[OAuth Callback POST] Got refresh token for user {user_id} - offline scope working!")
        else:
            logger.warning(f"[OAuth Callback POST] NO refresh token for user {user_id} - check if 'offline' scope is included!")
        
        # Get WHOOP user profile
        profile_response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/user/profile/basic",
            headers={"Authorization": f"Bearer {data['access_token']}"},
        )
        
        whoop_user_id = None
        if profile_response.status_code == 200:
            profile = profile_response.json()
            whoop_user_id = str(profile.get("user_id", ""))
        
        # Store tokens
        token_store[user_id] = {
            "access_token": data["access_token"],
            "refresh_token": refresh_token,
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat(),
            "whoop_user_id": whoop_user_id,
        }
        
        logger.info(f"Successfully connected WHOOP for user {user_id}, has_refresh_token={refresh_token is not None}")
        
        return CallbackResponse(
            success=True,
            whoop_user_id=whoop_user_id,
            connected_at=datetime.utcnow().isoformat(),
        )


@app.get("/api/v1/whoop/status/{user_id}", response_model=ConnectionStatus, tags=["Authentication"])
async def get_connection_status(
    user_id: str = Path(..., description="Your app's user ID", example="user_123"),
):
    """
    Check if a user has connected WHOOP.
    
    Returns connection status and token expiry info.
    """
    if user_id not in token_store:
        return ConnectionStatus(connected=False)
    
    token_data = token_store[user_id]
    expires_at = token_data.get("expires_at")
    
    return ConnectionStatus(
        connected=True,
        whoop_user_id=token_data.get("whoop_user_id"),
        expires_at=expires_at,
    )


@app.delete("/api/v1/whoop/disconnect/{user_id}", response_model=DisconnectResponse, tags=["Authentication"])
async def disconnect_whoop(
    user_id: str = Path(..., description="Your app's user ID", example="user_123"),
):
    """
    Disconnect WHOOP account for a user.
    
    Removes stored tokens. User will need to re-authorize.
    """
    if user_id not in token_store:
        raise HTTPException(status_code=404, detail="User not connected")
    
    token_store.pop(user_id)
    logger.info(f"Disconnected WHOOP for user {user_id}")
    
    return DisconnectResponse(success=True)


# ============== Data Fetching Endpoints ==============

@app.get("/api/v1/whoop/data/{user_id}", response_model=WhoopDataResponse, tags=["Data"])
async def get_whoop_data(
    user_id: str = Path(..., description="Your app's user ID", example="user_123"),
    days: int = Query(7, description="Number of days to fetch (from today backwards)", ge=1, le=30),
    timezone: Optional[TimezoneEnum] = Query(None, description="User's timezone for accurate day boundaries"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD) in user's timezone", example="2026-01-15"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD) in user's timezone", example="2026-01-22"),
    types: str = Query(
        "recovery,sleep,workout,cycle,profile,body_measurement",
        description="Comma-separated data types to fetch"
    ),
):
    """
    Fetch ALL WHOOP data for a user.
    
    This is the main endpoint to get WHOOP data. It fetches from WHOOP's v2 API
    using the stored OAuth tokens.
    
    **🌍 Timezone Handling:**
    - Provide `timezone` parameter for accurate day boundaries
    - If user is in Australia (UTC+11), Jan 22 local = Jan 21 13:00 UTC
    - Without timezone, defaults to UTC
    
    **📅 Date Options (choose one):**
    1. `days` - Fetch last N days from now (default: 7)
    2. `start_date` + `end_date` - Specific date range in YYYY-MM-DD format
    
    **Data Types:**
    - `recovery` - Daily recovery scores (HRV, resting HR, recovery %)
    - `sleep` - Sleep records (duration, stages, efficiency)
    - `workout` - Workout records (strain, HR, calories)
    - `cycle` - Physiological cycles
    - `profile` - User profile info
    - `body_measurement` - Height, weight, max HR
    
    **Example Response:**
    ```json
    {
      "recovery": [{"cycle_id": "...", "score": {"recovery_score": 85}}],
      "sleep": [{"id": "...", "start": "...", "end": "..."}],
      "workouts": [{"id": "...", "sport_id": 1, "score": {"strain": 15.5}}],
      "cycles": [...],
      "profile": {"user_id": 123, "first_name": "John"},
      "body_measurement": {"height_meter": 1.8, "weight_kilogram": 75}
    }
    ```
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired. Please reconnect.")
    
    headers = {"Authorization": f"Bearer {access_token}"}
    data_types = [t.strip().lower() for t in types.split(",")]
    
    # Determine timezone
    tz_name = timezone.value if timezone else "UTC"
    
    # Build query params for date range
    params = {}
    
    if start_date and end_date:
        # Use specific date range (convert from local to UTC)
        params["start"] = convert_local_date_to_utc(start_date, tz_name, end_of_day=False)
        params["end"] = convert_local_date_to_utc(end_date, tz_name, end_of_day=True)
        logger.info(f"Using date range: {start_date} to {end_date} ({tz_name}) -> UTC: {params['start']} to {params['end']}")
    else:
        # Use days parameter
        start_utc, end_utc = get_date_range_for_days(days, tz_name)
        params["start"] = start_utc
        params["end"] = end_utc
        logger.info(f"Using last {days} days in {tz_name} -> UTC: {start_utc} to {end_utc}")
    
    result = WhoopDataResponse()
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch Recovery data
        if "recovery" in data_types:
            try:
                response = await client.get(
                    f"{WHOOP_API_BASE_URL}/developer/v2/recovery",
                    headers=headers,
                    params=params,
                )
                if response.status_code == 200:
                    result.recovery = response.json().get("records", [])
                else:
                    logger.warning(f"Failed to fetch recovery: {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"Error fetching recovery: {e}")
        
        # Fetch Sleep data
        if "sleep" in data_types:
            try:
                response = await client.get(
                    f"{WHOOP_API_BASE_URL}/developer/v2/activity/sleep",
                    headers=headers,
                    params=params,
                )
                if response.status_code == 200:
                    result.sleep = response.json().get("records", [])
                else:
                    logger.warning(f"Failed to fetch sleep: {response.status_code}")
            except Exception as e:
                logger.error(f"Error fetching sleep: {e}")
        
        # Fetch Workout data
        if "workout" in data_types:
            try:
                response = await client.get(
                    f"{WHOOP_API_BASE_URL}/developer/v2/activity/workout",
                    headers=headers,
                    params=params,
                )
                if response.status_code == 200:
                    result.workouts = response.json().get("records", [])
                else:
                    logger.warning(f"Failed to fetch workouts: {response.status_code}")
            except Exception as e:
                logger.error(f"Error fetching workouts: {e}")
        
        # Fetch Cycle data
        if "cycle" in data_types:
            try:
                response = await client.get(
                    f"{WHOOP_API_BASE_URL}/developer/v2/cycle",
                    headers=headers,
                    params=params,
                )
                if response.status_code == 200:
                    result.cycles = response.json().get("records", [])
                else:
                    logger.warning(f"Failed to fetch cycles: {response.status_code}")
            except Exception as e:
                logger.error(f"Error fetching cycles: {e}")
        
        # Fetch User Profile
        if "profile" in data_types:
            try:
                response = await client.get(
                    f"{WHOOP_API_BASE_URL}/developer/v2/user/profile/basic",
                    headers=headers,
                )
                if response.status_code == 200:
                    result.profile = response.json()
                else:
                    logger.warning(f"Failed to fetch profile: {response.status_code}")
            except Exception as e:
                logger.error(f"Error fetching profile: {e}")
        
        # Fetch Body Measurement
        if "body_measurement" in data_types:
            try:
                response = await client.get(
                    f"{WHOOP_API_BASE_URL}/developer/v2/user/measurement/body",
                    headers=headers,
                )
                if response.status_code == 200:
                    result.body_measurement = response.json()
                else:
                    logger.warning(f"Failed to fetch body measurement: {response.status_code}")
            except Exception as e:
                logger.error(f"Error fetching body measurement: {e}")
    
    return result


# ============== Individual Data Endpoints ==============

@app.get("/api/v1/whoop/recovery/{user_id}", tags=["Data"])
async def get_recovery_data(
    user_id: str = Path(..., description="Your app's user ID"),
    days: int = Query(7, description="Number of days to fetch", ge=1, le=30),
    timezone: Optional[TimezoneEnum] = Query(None, description="User's timezone"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(25, description="Max records to return", le=25),
):
    """
    Get recovery data only.
    
    Recovery includes:
    - Recovery score (0-100%)
    - Resting heart rate
    - HRV (Heart Rate Variability)
    - SpO2 (blood oxygen)
    - Skin temperature
    
    **🌍 Timezone:** Provide timezone for accurate day boundaries.
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    tz_name = timezone.value if timezone else "UTC"
    
    params = {"limit": limit}
    if start_date and end_date:
        params["start"] = convert_local_date_to_utc(start_date, tz_name, end_of_day=False)
        params["end"] = convert_local_date_to_utc(end_date, tz_name, end_of_day=True)
    else:
        start_utc, end_utc = get_date_range_for_days(days, tz_name)
        params["start"] = start_utc
        params["end"] = end_utc
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/recovery",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch recovery data")
        
        return response.json()


@app.get("/api/v1/whoop/sleep/{user_id}", tags=["Data"])
async def get_sleep_data(
    user_id: str = Path(..., description="Your app's user ID"),
    days: int = Query(7, description="Number of days to fetch", ge=1, le=30),
    timezone: Optional[TimezoneEnum] = Query(None, description="User's timezone"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(25, description="Max records to return", le=25),
):
    """
    Get sleep data only.
    
    Sleep includes:
    - Total sleep duration
    - Sleep stages (light, deep/SWS, REM, awake)
    - Sleep efficiency %
    - Sleep performance %
    - Respiratory rate
    - Nap detection
    
    **🌍 Timezone:** Provide timezone for accurate day boundaries.
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    tz_name = timezone.value if timezone else "UTC"
    
    params = {"limit": limit}
    if start_date and end_date:
        params["start"] = convert_local_date_to_utc(start_date, tz_name, end_of_day=False)
        params["end"] = convert_local_date_to_utc(end_date, tz_name, end_of_day=True)
    else:
        start_utc, end_utc = get_date_range_for_days(days, tz_name)
        params["start"] = start_utc
        params["end"] = end_utc
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/activity/sleep",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch sleep data")
        
        return response.json()


@app.get("/api/v1/whoop/workout/{user_id}", tags=["Data"])
async def get_workout_data(
    user_id: str = Path(..., description="Your app's user ID"),
    days: int = Query(7, description="Number of days to fetch", ge=1, le=30),
    timezone: Optional[TimezoneEnum] = Query(None, description="User's timezone"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(25, description="Max records to return", le=25),
):
    """
    Get workout data only.
    
    Workout includes:
    - Strain score (0-21)
    - Average & max heart rate
    - Calories (kilojoules)
    - Distance
    - Sport type
    - Heart rate zones
    
    **🌍 Timezone:** Provide timezone for accurate day boundaries.
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    tz_name = timezone.value if timezone else "UTC"
    
    params = {"limit": limit}
    if start_date and end_date:
        params["start"] = convert_local_date_to_utc(start_date, tz_name, end_of_day=False)
        params["end"] = convert_local_date_to_utc(end_date, tz_name, end_of_day=True)
    else:
        start_utc, end_utc = get_date_range_for_days(days, tz_name)
        params["start"] = start_utc
        params["end"] = end_utc
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/activity/workout",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch workout data")
        
        return response.json()


@app.get("/api/v1/whoop/cycle/{user_id}", tags=["Data"])
async def get_cycle_data(
    user_id: str = Path(..., description="Your app's user ID"),
    days: int = Query(7, description="Number of days to fetch", ge=1, le=30),
    timezone: Optional[TimezoneEnum] = Query(None, description="User's timezone"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    limit: int = Query(25, description="Max records to return", le=25),
):
    """
    Get physiological cycle data.
    
    Cycles are WHOOP's daily tracking unit from wake to wake.
    
    **🌍 Timezone:** Provide timezone for accurate day boundaries.
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    tz_name = timezone.value if timezone else "UTC"
    
    params = {"limit": limit}
    if start_date and end_date:
        params["start"] = convert_local_date_to_utc(start_date, tz_name, end_of_day=False)
        params["end"] = convert_local_date_to_utc(end_date, tz_name, end_of_day=True)
    else:
        start_utc, end_utc = get_date_range_for_days(days, tz_name)
        params["start"] = start_utc
        params["end"] = end_utc
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/cycle",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch cycle data")
        
        return response.json()


# ============== User Endpoints ==============

@app.get("/api/v1/whoop/profile/{user_id}", tags=["User"])
async def get_user_profile(
    user_id: str = Path(..., description="Your app's user ID"),
):
    """
    Get WHOOP user profile.
    
    Returns basic profile info like name, email, WHOOP user ID.
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/user/profile/basic",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch profile")
        
        return response.json()


@app.get("/api/v1/whoop/body/{user_id}", tags=["User"])
async def get_body_measurement(
    user_id: str = Path(..., description="Your app's user ID"),
):
    """
    Get body measurements.
    
    Returns:
    - Height (meters)
    - Weight (kg)
    - Max heart rate
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{WHOOP_API_BASE_URL}/developer/v2/user/measurement/body",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch body measurements")
        
        return response.json()


# ============== Run Server ==============

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
