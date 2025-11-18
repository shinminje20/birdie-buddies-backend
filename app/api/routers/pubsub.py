from __future__ import annotations
import base64
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ...config import get_settings
from ...db import get_db
from ...repos import gmail_tokens
from ...services import interac_parser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pubsub", tags=["pubsub"])

S = get_settings()

# Gmail OAuth scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]


class PubSubMessage(BaseModel):
    """Pub/Sub push message format from Google"""
    message: dict
    subscription: str


@router.post("/gmail-notification")
async def handle_gmail_notification(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook endpoint for Gmail Pub/Sub notifications.
    Google Cloud Pub/Sub pushes notifications here when new emails arrive.
    """
    try:
        # Parse Pub/Sub message
        body = await request.json()
        logger.info(f"Received Pub/Sub notification: {body}")

        # Extract message data
        if 'message' not in body:
            logger.warning("No message in Pub/Sub payload")
            return {"status": "ignored", "reason": "no_message"}

        message = body['message']

        # Decode the Pub/Sub message data (base64 encoded JSON)
        if 'data' in message:
            decoded_data = base64.b64decode(message['data']).decode('utf-8')
            notification_data = json.loads(decoded_data)
            logger.info(f"Decoded notification data: {notification_data}")
        else:
            # Gmail push notifications sometimes don't have data, just attributes
            notification_data = message.get('attributes', {})
            logger.info(f"Using attributes as notification data: {notification_data}")

        # Extract email address and historyId from notification
        email_address = notification_data.get('emailAddress')
        history_id = notification_data.get('historyId')

        if not email_address or not history_id:
            logger.warning(f"Missing emailAddress or historyId in notification: {notification_data}")
            return {"status": "ignored", "reason": "missing_fields"}

        # Get stored Gmail token
        token_record = await gmail_tokens.get_active_token(db)
        if not token_record:
            logger.error("No active Gmail token found")
            return {"status": "error", "reason": "no_token_configured"}

        # Check if this historyId is newer than what we've processed
        if token_record.history_id and int(history_id) <= int(token_record.history_id):
            logger.info(f"Already processed historyId {history_id}, skipping")
            return {"status": "ignored", "reason": "already_processed"}

        # Get access token from refresh token
        credentials = Credentials(
            token=None,
            refresh_token=token_record.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=S.GOOGLE_CLIENT_ID,
            client_secret=S.GOOGLE_CLIENT_SECRET,
            scopes=SCOPES
        )

        # Build Gmail service
        service = build('gmail', 'v1', credentials=credentials)

        # Fetch history since last processed historyId
        start_history_id = token_record.history_id or history_id

        history_response = service.users().history().list(
            userId='me',
            startHistoryId=start_history_id,
            historyTypes=['messageAdded'],
            labelId='INBOX'
        ).execute()

        changes = history_response.get('history', [])
        logger.info(f"Found {len(changes)} history changes")

        processed_count = 0

        # Process each history change
        for change in changes:
            messages_added = change.get('messagesAdded', [])

            for msg_added in messages_added:
                message_data = msg_added.get('message', {})
                message_id = message_data.get('id')

                if not message_id:
                    continue

                # Fetch full message details
                full_message = service.users().messages().get(
                    userId='me',
                    id=message_id,
                    format='full'
                ).execute()

                # Check if it's an Interac e-Transfer email
                if await interac_parser.is_interac_email(full_message):
                    logger.info(f"Processing Interac e-Transfer email: {message_id}")

                    # Parse and process the deposit
                    await interac_parser.process_interac_deposit(
                        db=db,
                        message=full_message,
                        message_id=message_id
                    )
                    processed_count += 1
                else:
                    logger.debug(f"Skipping non-Interac email: {message_id}")

        # Update the last processed historyId
        await gmail_tokens.update_history_id(db, history_id)
        await db.commit()

        logger.info(f"Successfully processed {processed_count} Interac emails")

        return {
            "status": "success",
            "processed_emails": processed_count,
            "history_id": history_id
        }

    except Exception as e:
        logger.error(f"Error processing Gmail notification: {str(e)}", exc_info=True)
        # Return 200 to prevent Pub/Sub from retrying
        # (we'll handle retries differently if needed)
        return {
            "status": "error",
            "error": str(e)
        }
