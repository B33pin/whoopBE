"""
WHOOP Backend Service - Main Application

FastAPI backend for WHOOP OAuth integration.
Handles OAuth flow, token storage, and data fetching for Nutrogen mobile app.
"""

import os
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

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

# FastAPI App
app = FastAPI(
    title="WHOOP Backend Service",
    description="OAuth integration service for WHOOP API",
    version="1.0.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== Pydantic Models ==============

class AuthUrlRequest(BaseModel):
    user_id: str


class AuthUrlResponse(BaseModel):
    authorization_url: str
    state: str


class CallbackRequest(BaseModel):
    code: str
    state: str


class CallbackResponse(BaseModel):
    success: bool
    whoop_user_id: Optional[str] = None
    connected_at: Optional[str] = None
    error: Optional[str] = None


class DisconnectResponse(BaseModel):
    success: bool


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


# ============== API Endpoints ==============

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway."""
    return {"status": "healthy", "service": "whoop-backend"}


@app.post("/api/v1/whoop/auth-url", response_model=AuthUrlResponse)
async def get_auth_url(request: AuthUrlRequest):
    """
    Generate WHOOP OAuth authorization URL.
    
    The mobile app calls this to get the URL to open in browser.
    """
    if not WHOOP_CLIENT_ID:
        raise HTTPException(status_code=500, detail="WHOOP_CLIENT_ID not configured")
    
    state = generate_state()
    state_store[state] = request.user_id
    
    # WHOOP OAuth scopes
    scopes = "read:recovery read:sleep read:workout read:cycles read:profile"
    
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


@app.get("/api/v1/whoop/callback")
async def oauth_callback_redirect(
    code: str = Query(...),
    state: str = Query(...),
):
    """
    OAuth callback endpoint (called by WHOOP after user approves).
    
    This exchanges the code for tokens and redirects to the mobile app.
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


@app.post("/api/v1/whoop/callback", response_model=CallbackResponse)
async def oauth_callback_manual(request: CallbackRequest):
    """
    Manual OAuth callback (alternative to redirect).
    
    Mobile app can call this directly if handling deep link parsing.
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


@app.get("/api/v1/whoop/data/{user_id}")
async def get_whoop_data(
    user_id: str,
    start: Optional[str] = Query(None, description="Start date ISO8601"),
    end: Optional[str] = Query(None, description="End date ISO8601"),
    types: str = Query("recovery,sleep,workout", description="Data types to fetch"),
):
    """
    Fetch WHOOP data for a user.
    
    Uses stored tokens to call WHOOP v2 API.
    """
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        raise HTTPException(status_code=401, detail="User not connected or token expired")
    
    headers = {"Authorization": f"Bearer {access_token}"}
    data_types = [t.strip() for t in types.split(",")]
    
    # Build query params
    params = {}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    
    result = {}
    
    async with httpx.AsyncClient() as client:
        # Fetch Recovery data
        if "recovery" in data_types:
            response = await client.get(
                f"{WHOOP_API_BASE_URL}/developer/v2/recovery",
                headers=headers,
                params=params,
            )
            if response.status_code == 200:
                result["recovery"] = response.json().get("records", [])
            else:
                logger.warning(f"Failed to fetch recovery: {response.status_code}")
                result["recovery"] = []
        
        # Fetch Sleep data
        if "sleep" in data_types:
            response = await client.get(
                f"{WHOOP_API_BASE_URL}/developer/v2/activity/sleep",
                headers=headers,
                params=params,
            )
            if response.status_code == 200:
                result["sleep"] = response.json().get("records", [])
            else:
                logger.warning(f"Failed to fetch sleep: {response.status_code}")
                result["sleep"] = []
        
        # Fetch Workout data
        if "workout" in data_types:
            response = await client.get(
                f"{WHOOP_API_BASE_URL}/developer/v2/activity/workout",
                headers=headers,
                params=params,
            )
            if response.status_code == 200:
                result["workouts"] = response.json().get("records", [])
            else:
                logger.warning(f"Failed to fetch workouts: {response.status_code}")
                result["workouts"] = []
        
        # Fetch Cycle data
        if "cycle" in data_types:
            response = await client.get(
                f"{WHOOP_API_BASE_URL}/developer/v2/cycle",
                headers=headers,
                params=params,
            )
            if response.status_code == 200:
                result["cycles"] = response.json().get("records", [])
            else:
                logger.warning(f"Failed to fetch cycles: {response.status_code}")
                result["cycles"] = []
    
    return result


@app.delete("/api/v1/whoop/disconnect/{user_id}", response_model=DisconnectResponse)
async def disconnect_whoop(user_id: str):
    """
    Disconnect WHOOP account for a user.
    
    Revokes tokens and removes from storage.
    """
    if user_id not in token_store:
        raise HTTPException(status_code=404, detail="User not connected")
    
    token_data = token_store.pop(user_id)
    
    # Optionally revoke token with WHOOP (if they support it)
    # For now, just remove from our store
    
    logger.info(f"Disconnected WHOOP for user {user_id}")
    
    return DisconnectResponse(success=True)


@app.get("/api/v1/whoop/status/{user_id}")
async def get_connection_status(user_id: str):
    """Check if user has connected WHOOP."""
    if user_id not in token_store:
        return {"connected": False}
    
    token_data = token_store[user_id]
    expires_at = token_data.get("expires_at")
    
    return {
        "connected": True,
        "whoop_user_id": token_data.get("whoop_user_id"),
        "expires_at": expires_at,
    }


# ============== Run Server ==============

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
