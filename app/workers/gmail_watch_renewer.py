from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from ..config import get_settings
from ..db import SessionLocal
from ..redis_client import redis
from ..repos import gmail_tokens
from ..observability.heartbeat import beat
from ..observability.logging import setup_logging

S = get_settings()
log = logging.getLogger("worker.gmail_watch_renewer")

# Gmail OAuth scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]

# Check every 1 hours
# CHECK_INTERVAL_SEC = 1 * 60 * 60
CHECK_INTERVAL_SEC = 4 * 60 * 60 # 4 hours

# Renew if expiring within 24 hours
RENEW_THRESHOLD_HOURS = 24


def _lock_key() -> str:
    return "lock:gmail_watch_renewer"


async def _acquire_lock() -> bool:
    """
    Acquire distributed lock to ensure only one worker instance runs.
    Only one instance performs the check; others idle.

    Returns:
        True if lock acquired, False otherwise
    """
    # Lock TTL: 80% of check interval, minimum 10 seconds
    lock_ttl = max(10, int(CHECK_INTERVAL_SEC * 0.8))
    return await redis.set(_lock_key(), "1", ex=lock_ttl, nx=True) is True


async def run_once():
    """Check and renew Gmail watch if needed"""
    log.info("🔄 Gmail watch renewer tick started")

    # Acquire short lock; if taken, just skip this tick
    log.debug("Attempting to acquire lock...")
    if not await _acquire_lock():
        log.debug("Lock already held by another instance, skipping this tick")
        return False

    log.info("✅ Lock acquired, proceeding with check")

    async with SessionLocal() as db:
        try:
            # Get active Gmail token
            log.debug("Fetching active Gmail token from database...")
            token_record = await gmail_tokens.get_active_token(db)

            if not token_record:
                log.warning("⚠️  No active Gmail token found, skipping renewal check")
                log.info("💡 Run OAuth flow first: GET /gmail/authorize")
                return False

            log.info(f"✅ Found Gmail token for: {token_record.email}")

            # Check if watch needs renewal
            log.debug("Checking if watch needs renewal...")
            if not token_record.watch_expiration:
                log.warning("⚠️  Watch expiration not set, will attempt renewal")
                needs_renewal = True
            else:
                # Calculate time until expiration
                now = datetime.now(timezone.utc)
                expiration = token_record.watch_expiration

                # Ensure expiration is timezone-aware for comparison
                if expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=timezone.utc)

                time_until_expiration = expiration - now
                hours_remaining = time_until_expiration.total_seconds() / 3600
                needs_renewal = time_until_expiration < timedelta(hours=RENEW_THRESHOLD_HOURS)

                if needs_renewal:
                    log.info(
                        f"⏰ Gmail watch expires in {hours_remaining:.1f} hours "
                        f"(threshold: {RENEW_THRESHOLD_HOURS}h), renewing now..."
                    )
                else:
                    log.info(
                        f"✅ Gmail watch still valid for {hours_remaining:.1f} hours "
                        f"(threshold: {RENEW_THRESHOLD_HOURS}h), no renewal needed"
                    )

            if not needs_renewal:
                log.info("📌 Skipping renewal check until next tick")
                return False

            log.info("🔧 Starting Gmail watch renewal...")

            # Renew the watch
            # Create credentials from refresh token and force a refresh to get a valid access token
            log.debug("Creating OAuth credentials from refresh token...")
            credentials = Credentials(
                token=None,
                refresh_token=token_record.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=S.GOOGLE_CLIENT_ID,
                client_secret=S.GOOGLE_CLIENT_SECRET,
                scopes=SCOPES
            )

            try:
                credentials.refresh(Request())
            except RefreshError as e:
                log.error("❌ Failed to refresh Gmail OAuth credentials. Reauthorize via /gmail/authorize. Error: %s", e)
                return False

            # Build Gmail service
            log.debug("Building Gmail API service...")
            service = build('gmail', 'v1', credentials=credentials)

            # Renew watch
            if not S.GOOGLE_PUBSUB_TOPIC:
                log.error("❌ GOOGLE_PUBSUB_TOPIC not configured")
                return False

            log.info(f"📤 Calling Gmail API to renew watch for topic: {S.GOOGLE_PUBSUB_TOPIC}")
            watch_request = {
                'labelIds': ['INBOX'],
                'topicName': S.GOOGLE_PUBSUB_TOPIC
            }
            watch_response = service.users().watch(userId='me', body=watch_request).execute()

            log.debug(f"Gmail API response: {watch_response}")

            # Update expiration AND historyId
            expiration_ms = int(watch_response.get('expiration', 0))
            # Use UTC timezone explicitly to avoid local timezone issues
            watch_expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc) if expiration_ms else None
            history_id = watch_response.get('historyId')

            log.info(f"📊 New watch expiration: {watch_expiration}")
            log.info(f"📊 New historyId: {history_id}")

            # Update database
            log.debug("Updating database with new watch info...")
            await gmail_tokens.update_watch_expiration(db, watch_expiration)
            if history_id:
                await gmail_tokens.update_history_id(db, history_id)
            await db.commit()

            log.info(
                f"✅ Successfully renewed Gmail watch for {token_record.email}. "
                f"New expiration: {watch_expiration}, historyId: {history_id}"
            )

            return True

        except Exception as e:
            log.error(f"Failed to renew Gmail watch: {str(e)}", exc_info=True)
            return False


async def run_forever():
    """Run the Gmail watch renewal check periodically"""
    log.info("=" * 60)
    log.info("🚀 Gmail Watch Renewer Worker Starting...")
    log.info("=" * 60)
    log.info(f"⏱️  Check interval: {CHECK_INTERVAL_SEC/60:.1f} minutes")
    log.info(f"⏰ Renewal threshold: {RENEW_THRESHOLD_HOURS} hours before expiration")
    log.info(f"🔒 Lock TTL: {max(10, int(CHECK_INTERVAL_SEC * 0.8))} seconds")
    log.info("=" * 60)

    # Start heartbeat
    log.debug("Starting heartbeat task...")
    asyncio.create_task(beat("hb:gmail_watch_renewer"))

    log.info("✅ Worker initialized successfully, entering main loop")

    while True:
        try:
            await run_once()
        except Exception as e:
            log.exception("❌ gmail_watch_renewer error: %s", e)

        log.debug(f"💤 Sleeping for {CHECK_INTERVAL_SEC} seconds until next check...")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def main():
    setup_logging()
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
