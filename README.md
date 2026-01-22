# WHOOP Backend Service

A FastAPI backend service for WHOOP OAuth integration with Nutrogen mobile app.

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create `.env` file from example:
```bash
cp .env.example .env
```

3. Fill in WHOOP credentials in `.env`:
- Get these from [developer.whoop.com](https://developer.whoop.com)

4. Run locally:
```bash
uvicorn main:app --reload
```

## Deploy to Render.com (Free)

1. Push this folder to a GitHub repo
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Render will auto-detect the `render.yaml` config
5. Add environment variables in dashboard:
   - `WHOOP_CLIENT_ID`
   - `WHOOP_CLIENT_SECRET`
   - `WHOOP_REDIRECT_URI` = `https://your-app.onrender.com/api/v1/whoop/callback`
6. Deploy!

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/whoop/auth-url` | POST | Get WHOOP OAuth URL |
| `/api/v1/whoop/callback` | GET/POST | OAuth callback |
| `/api/v1/whoop/data/{user_id}` | GET | Fetch WHOOP data |
| `/api/v1/whoop/status/{user_id}` | GET | Check connection |
| `/api/v1/whoop/disconnect/{user_id}` | DELETE | Disconnect |

