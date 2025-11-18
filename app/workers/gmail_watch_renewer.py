from __future__ import annotations
import asyncio
import logging
import secrets
from datetime import datetime, timezone, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..config import get_settings
from ..db import SessionLocal
from ..redis_client import redis
from ..repos import gmail_tokens
from ..observability.heartbeat import beat

S = get_settings()
log = logging.getLogger("worker.gmail_watch_renewer")

# Gmail OAuth scopes
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]

# Check every 1 hours
# CHECK_INTERVAL_SEC = 1 * 60 * 60
CHECK_INTERVAL_SEC = 1 * 60 # 1min

# Renew if expiring within 24 hours
RENEW_THRESHOLD_HOURS = 24


def _lock_key() -> str:
    return "lock:gmail_watch_renewer"


async def _acquire_lock() -> str | None:
    """
    Acquire distributed lock to ensure only one worker instance runs.

    Returns:
        Lock token (random string) if acquired, None otherwise
    """
    # Lock TTL slightly less than check interval
    lock_ttl = CHECK_INTERVAL_SEC - 60
    # Generate unique token to identify this lock owner
    lock_token = secrets.token_hex(16)

    # Try to acquire lock with our token
    acquired = await redis.set(_lock_key(), lock_token, ex=lock_ttl, nx=True)
    return lock_token if acquired else None


async def _release_lock(lock_token: str) -> None:
    """
    Release lock only if we still own it.

    Args:
        lock_token: The token we used to acquire the lock
    """
    # Lua script to atomically check and delete only if value matches
    lua_script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    await redis.eval(lua_script, 1, _lock_key(), lock_token)


async def run_once():
    """Check and renew Gmail watch if needed"""
    # Acquire lock
    lock_token = await _acquire_lock()
    if not lock_token:
        log.debug("Another instance is running, skipping")
        return False

    # Always release lock when done (success or failure)
    try:
        async with SessionLocal() as db:
            # Get active Gmail token
            token_record = await gmail_tokens.get_active_token(db)

            if not token_record:
                log.warning("No active Gmail token found, skipping renewal check")
                return False

            # Check if watch needs renewal
            if not token_record.watch_expiration:
                log.warning("Watch expiration not set, will attempt renewal")
                needs_renewal = True
            else:
                # Calculate time until expiration
                now = datetime.now(timezone.utc)
                expiration = token_record.watch_expiration

                # Ensure expiration is timezone-aware for comparison
                if expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=timezone.utc)

                time_until_expiration = expiration - now
                needs_renewal = time_until_expiration < timedelta(hours=RENEW_THRESHOLD_HOURS)

                if needs_renewal:
                    log.info(
                        f"Gmail watch expires in {time_until_expiration.total_seconds()/3600:.1f} hours, "
                        f"renewing now..."
                    )
                else:
                    log.debug(
                        f"Gmail watch expires in {time_until_expiration.total_seconds()/3600:.1f} hours, "
                        f"no renewal needed yet"
                    )

            if not needs_renewal:
                return False

            # Renew the watch
            # Create credentials from refresh token
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

            # Renew watch
            if not S.GOOGLE_PUBSUB_TOPIC:
                log.error("GOOGLE_PUBSUB_TOPIC not configured")
                return False

            watch_request = {
                'labelIds': ['INBOX'],
                'topicName': S.GOOGLE_PUBSUB_TOPIC
            }
            watch_response = service.users().watch(userId='me', body=watch_request).execute()

            # Update expiration AND historyId
            expiration_ms = int(watch_response.get('expiration', 0))
            # Use UTC timezone explicitly to avoid local timezone issues
            watch_expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc) if expiration_ms else None
            history_id = watch_response.get('historyId')

            # Update database
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
    finally:
        # Release lock only if we still own it
        await _release_lock(lock_token)


async def run_forever():
    """Run the Gmail watch renewal check periodically"""
    # Start heartbeat
    asyncio.create_task(beat("hb:gmail_watch_renewer"))

    log.info(f"Gmail watch renewer started. Checking every {CHECK_INTERVAL_SEC/3600:.1f} hours.")

    while True:
        try:
            await run_once()
        except Exception as e:
            log.exception("gmail_watch_renewer error: %s", e)

        await asyncio.sleep(CHECK_INTERVAL_SEC)


def main():
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
