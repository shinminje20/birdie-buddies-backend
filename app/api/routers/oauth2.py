from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from ...config import get_settings
from ...db import get_db
from ...repos import gmail_tokens
from ...redis_client import redis

router = APIRouter(tags=["oauth2"])

S = get_settings()

# Gmail OAuth scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]


def get_oauth_flow():
    """Create OAuth flow for Gmail authorization"""
    if not S.GOOGLE_CLIENT_ID or not S.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth credentials not configured"
        )

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": S.GOOGLE_CLIENT_ID,
                "client_secret": S.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [S.GMAIL_OAUTH_REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        redirect_uri=S.GMAIL_OAUTH_REDIRECT_URI
    )
    return flow


class OAuthCallbackResponse(BaseModel):
    email: str
    watch_expiration: Optional[datetime]
    success: bool


@router.get("/oauth2/google/callback", response_model=OAuthCallbackResponse)
async def google_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db)
):
    """
    OAuth callback endpoint. Google redirects here after user authorizes.
    Exchanges code for tokens and sets up Gmail watch.
    """
    # CSRF Protection: Validate state
    state_key = f"gmail_oauth_state:{state}"
    stored_state = await redis.get(state_key)

    if not stored_state:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state. Please restart the authorization flow."
        )

    # Delete state (one-time use)
    await redis.delete(state_key)

    try:
        # Exchange authorization code for tokens
        flow = get_oauth_flow()
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # Get user's email from Gmail API
        service = build('gmail', 'v1', credentials=credentials)
        profile = service.users().getProfile(userId='me').execute()
        email_address = profile['emailAddress']

        # Validate it's the business Gmail account
        if email_address.lower() != "bdbirdies@gmail.com":
            raise HTTPException(
                status_code=400,
                detail="Only the business Gmail account (bdbirdies@gmail.com) can be connected"
            )

        # Set up Gmail watch
        if not S.GOOGLE_PUBSUB_TOPIC:
            raise HTTPException(
                status_code=500,
                detail="GOOGLE_PUBSUB_TOPIC not configured"
            )

        watch_request = {
            'labelIds': ['INBOX'],
            'topicName': S.GOOGLE_PUBSUB_TOPIC
        }
        watch_response = service.users().watch(userId='me', body=watch_request).execute()

        # Calculate watch expiration (Gmail watch expires in ~7 days)
        # expiration is in milliseconds, convert to UTC datetime
        expiration_ms = int(watch_response.get('expiration', 0))
        watch_expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc) if expiration_ms else None

        # Get initial historyId
        history_id = watch_response.get('historyId')

        # Store refresh token in database
        await gmail_tokens.upsert_token(
            db,
            email=email_address,
            refresh_token=credentials.refresh_token,
            history_id=history_id,
            watch_expiration=watch_expiration
        )
        await db.commit()

        return OAuthCallbackResponse(
            email=email_address,
            watch_expiration=watch_expiration,
            success=True
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to complete OAuth flow: {str(e)}"
        )
