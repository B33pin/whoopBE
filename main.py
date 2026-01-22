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
from datetime import datetime, timedelta
from typing import Optional, List
from urllib.parse import urlencode

import httpx
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
    """Refresh the access token if expired."""
    if user_id not in token_store:
        return None
    
    token_data = token_store[user_id]
    expires_at = token_data.get("expires_at")
    
    # Check if token is expired (with 5 min buffer)
    if expires_at and datetime.fromisoformat(expires_at) > datetime.utcnow() + timedelta(minutes=5):
        return token_data["access_token"]
    
    # Refresh the token
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
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
            logger.error(f"Token refresh failed: {response.text}")
            return None
        
        data = response.json()
        expires_in = data.get("expires_in", 3600)
        
        token_store[user_id] = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat(),
            "whoop_user_id": token_data.get("whoop_user_id"),
        }
        
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
    
    # WHOOP OAuth scopes
    scopes = "read:recovery read:sleep read:workout read:cycles read:profile read:body_measurement"
    
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
            "refresh_token": data.get("refresh_token"),
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat(),
            "whoop_user_id": whoop_user_id,
        }
        
        logger.info(f"Successfully connected WHOOP for user {user_id}")
        
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
            "refresh_token": data.get("refresh_token"),
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat(),
            "whoop_user_id": whoop_user_id,
        }
        
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
    start: Optional[str] = Query(None, description="Start date (ISO8601)", example="2026-01-15T00:00:00Z"),
    end: Optional[str] = Query(None, description="End date (ISO8601)", example="2026-01-22T23:59:59Z"),
    types: str = Query(
        "recovery,sleep,workout,cycle,profile,body_measurement",
        description="Comma-separated data types to fetch"
    ),
):
    """
    Fetch ALL WHOOP data for a user.
    
    This is the main endpoint to get WHOOP data. It fetches from WHOOP's v2 API
    using the stored OAuth tokens.
    
    **Data Types:**
    - `recovery` - Daily recovery scores (HRV, resting HR, recovery %)
    - `sleep` - Sleep records (duration, stages, efficiency)
    - `workout` - Workout records (strain, HR, calories)
    - `cycle` - Physiological cycles
    - `profile` - User profile info
    - `body_measurement` - Height, weight, max HR
    
    **Date Range:**
    - If no dates provided, returns last 7 days
    - Dates should be ISO8601 format
    
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
    
    # Build query params for date range
    params = {}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    
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
    start: Optional[str] = Query(None, description="Start date (ISO8601)"),
    end: Optional[str] = Query(None, description="End date (ISO8601)"),
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
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    params = {"limit": limit}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    
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
    start: Optional[str] = Query(None, description="Start date (ISO8601)"),
    end: Optional[str] = Query(None, description="End date (ISO8601)"),
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
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    params = {"limit": limit}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    
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
    start: Optional[str] = Query(None, description="Start date (ISO8601)"),
    end: Optional[str] = Query(None, description="End date (ISO8601)"),
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
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    params = {"limit": limit}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    
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
    start: Optional[str] = Query(None, description="Start date (ISO8601)"),
    end: Optional[str] = Query(None, description="End date (ISO8601)"),
    limit: int = Query(25, description="Max records to return", le=25),
):
    """
    Get physiological cycle data.
    
    Cycles are WHOOP's daily tracking unit from wake to wake.
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    params = {"limit": limit}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    
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
