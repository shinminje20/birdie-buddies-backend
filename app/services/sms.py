from __future__ import annotations

import asyncio
import logging
from typing import Optional

from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from ..config import get_settings

logger = logging.getLogger(__name__)


class TwilioSMSService:
    """Thin wrapper around the Twilio REST client with async-friendly send."""

    def __init__(self) -> None:
        settings = get_settings()
        self._from_number: Optional[str] = settings.TWILIO_FROM_NUMBER
        if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and self._from_number:
            self._client: Optional[Client] = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        else:
            self._client = None
            missing = [
                key
                for key, value in [
                    ("TWILIO_ACCOUNT_SID", settings.TWILIO_ACCOUNT_SID),
                    ("TWILIO_AUTH_TOKEN", settings.TWILIO_AUTH_TOKEN),
                    ("TWILIO_FROM_NUMBER", self._from_number),
                ]
                if not value
            ]
            if missing:
                logger.info("Twilio SMS disabled; missing settings: %s", ", ".join(missing))

    @property
    def enabled(self) -> bool:
        return bool(self._client and self._from_number)

    async def send_sms(self, *, to: str, body: str) -> bool:
        """Send SMS to the provided destination number."""
        if not self._client or not self._from_number:
            logger.debug("SMS send skipped because Twilio is not configured.")
            return False

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._client.messages.create(  # type: ignore[attr-defined]
                    from_=self._from_number,
                    to=to,
                    body=body,
                ),
            )
            return True
        except TwilioException as exc:
            logger.warning("Twilio SMS send failed: %s", exc)
            return False


sms_service = TwilioSMSService()
