from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Registration, Session as SessionModel, User
from ..redis_client import redis
from ..services.sms import sms_service

logger = logging.getLogger(__name__)

# Message templates by event type
# Each tuple: (template_with_title, template_without_title)
MESSAGE_TEMPLATES = {
    "registration_confirmed": (
        "Birdie Buddies - Your session '{title}' has been confirmed",
        "Birdie Buddies - Your session has been confirmed",
    ),
    "registration_promoted": (
        "Birdie Buddies - Your session '{title}' has been confirmed",
        "Birdie Buddies - Your session has been confirmed",
    ),
    "registration_waitlisted": (
        "Birdie Buddies - You are waitlisted for '{title}'",
        "Birdie Buddies - You are waitlisted",
    ),
}


async def _get_registration_data(registration_id: uuid.UUID) -> Optional[Tuple[str, uuid.UUID, Optional[str]]]:
    """Fetch the host phone number and session title for a registration, skipping guests."""
    async with SessionLocal() as db:
        row = await db.execute(
            select(Registration, User, SessionModel)
            .join(User, Registration.host_user_id == User.id)
            .join(SessionModel, Registration.session_id == SessionModel.id)
            .where(Registration.id == registration_id)
        )
        result = row.first()

    if not result:
        return None

    registration, user, session = result
    if not registration.is_host:
        return None
    if not user.phone:
        logger.debug("User %s has no phone on file; skipping SMS.", registration.host_user_id)
        return None

    return (user.phone, registration.host_user_id, session.title)


async def _handle_payload(payload: Dict[str, Any]) -> None:
    event_type = payload.get("type")
    if event_type not in MESSAGE_TEMPLATES:
        return

    reg_id = payload.get("registration_id")
    if not reg_id:
        return

    try:
        reg_uuid = uuid.UUID(reg_id)
    except Exception:
        logger.debug("Invalid registration_id in payload: %s", reg_id)
        return

    data = await _get_registration_data(reg_uuid)
    if not data:
        return

    phone, host_id, session_title = data

    # Build message with or without session title
    template_with_title, template_without_title = MESSAGE_TEMPLATES[event_type]
    if session_title:
        message = template_with_title.format(title=session_title)
    else:
        message = template_without_title

    if sms_service.enabled:
        sent = await sms_service.send_sms(to=phone, body=message)
        if sent:
            logger.info("Sent %s SMS to %s for registration %s", event_type, phone, reg_uuid)
    else:
        logger.debug("Twilio disabled; would have sent '%s' to %s for user %s", message, phone, host_id)


def _decode_payload(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Ignoring non-JSON payload: %s", raw)
        return None


async def main_loop() -> None:
    pubsub = redis.pubsub()
    await pubsub.psubscribe("session:*")
    logger.info("SMS notifier subscribed to session:* channels")

    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if not msg:
                await asyncio.sleep(0.5)
                continue
            if msg.get("type") != "pmessage":
                continue
            payload = _decode_payload(msg.get("data"))
            if payload:
                try:
                    await _handle_payload(payload)
                except Exception as exc:
                    logger.exception("Failed to handle SMS payload: %s", exc)
                    await asyncio.sleep(0.5)
    finally:
        try:
            await pubsub.punsubscribe("session:*")
        finally:
            await pubsub.close()


def main() -> None:
    asyncio.run(main_loop())


if __name__ == "__main__":
    main()
