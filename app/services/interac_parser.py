from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from ..repos import users as users_repo, ledger_repo
from ..observability.logging import logger


async def is_interac_email(message: dict) -> bool:
    """
    Check if email is an Interac e-Transfer notification.

    Args:
        message: Gmail API message object

    Returns:
        True if it's an Interac e-Transfer email
    """
    headers = message.get('payload', {}).get('headers', [])

    for header in headers:
        name = header.get('name', '').lower()
        value = header.get('value', '')

        # Check subject line
        if name == 'subject':
            # Match pattern: "Interac e-Transfer: You've received $XX.XX from NAME"
            if 'interac e-transfer' in value.lower() and 'received' in value.lower():
                return True

        # Check from address
        if name == 'from':
            if 'notify@payments.interac.ca' in value.lower():
                return True

    return False


def extract_sender_info(headers: list) -> Optional[Dict[str, str]]:
    """
    Extract sender name and reply-to email from headers.

    Example reply-to: "Jun Soo Kim <arpaker@hotmail.com>"

    Returns:
        Dict with 'name' and 'email' keys, or None if not found
    """
    reply_to = None

    for header in headers:
        name = header.get('name', '').lower()
        value = header.get('value', '')

        if name == 'reply-to':
            reply_to = value
            break

    if not reply_to:
        logger.warning("No reply-to header found in Interac email")
        return None

    # Parse "Name <email@example.com>" format
    # Pattern: "Name Name <email@example.com>"
    match = re.match(r'^(.+?)\s*<([^>]+)>$', reply_to.strip())

    if match:
        sender_name = match.group(1).strip()
        sender_email = match.group(2).strip().lower()

        return {
            'name': sender_name,
            'email': sender_email
        }

    # If no angle brackets, assume entire string is email
    if '@' in reply_to:
        return {
            'name': '',
            'email': reply_to.strip().lower()
        }

    logger.warning(f"Could not parse reply-to header: {reply_to}")
    return None


def extract_amount_from_subject(subject: str) -> Optional[int]:
    """
    Extract dollar amount from subject line.

    Example: "Interac e-Transfer: You've received $13.50 from Jun Soo Kim..."
    Returns: 1350 (amount in cents)

    Args:
        subject: Email subject line

    Returns:
        Amount in cents, or None if not found
    """
    # Pattern: $XX.XX or $X,XXX.XX
    pattern = r'\$\s?([\d,]+(?:\.\d{2})?)'
    match = re.search(pattern, subject)

    if not match:
        logger.warning(f"Could not extract amount from subject: {subject}")
        return None

    amount_str = match.group(1).replace(',', '')  # Remove commas

    try:
        # Use Decimal to avoid float precision issues
        # Decimal("13.50") * 100 = Decimal("1350.00")
        amount_dollars = Decimal(amount_str)
        amount_cents = int(amount_dollars * 100)
        return amount_cents
    except (ValueError, TypeError, InvalidOperation) as e:
        logger.error(f"Error parsing amount '{amount_str}': {e}")
        return None


async def process_interac_deposit(
    db: AsyncSession,
    message: dict,
    message_id: str
) -> bool:
    """
    Process an Interac e-Transfer email and update user's wallet.

    Args:
        db: Database session
        message: Gmail API message object
        message_id: Gmail message ID (for idempotency)

    Returns:
        True if successfully processed, False otherwise
    """
    try:
        headers = message.get('payload', {}).get('headers', [])

        # Extract subject
        subject = None
        for header in headers:
            if header.get('name', '').lower() == 'subject':
                subject = header.get('value', '')
                break

        if not subject:
            logger.error(f"No subject found in message {message_id}")
            return False

        # Extract amount from subject
        amount_cents = extract_amount_from_subject(subject)
        if not amount_cents:
            logger.error(f"Could not extract amount from subject: {subject}")
            return False

        # Extract sender info from reply-to
        sender_info = extract_sender_info(headers)
        if not sender_info or not sender_info.get('email'):
            logger.error(f"Could not extract sender email from message {message_id}")
            return False

        sender_email = sender_info['email']
        sender_name = sender_info.get('name', '')

        logger.info(
            f"Processing deposit: ${amount_cents/100:.2f} from "
            f"{sender_name} <{sender_email}>"
        )

        # Find user by email
        user = await users_repo.get_by_email(db, sender_email)

        if not user:
            logger.warning(
                f"No user found with email {sender_email}. "
                f"Deposit of ${amount_cents/100:.2f} NOT applied."
            )
            # TODO: Could send notification to admin or create pending deposit record
            return False

        # Create idempotency key from Gmail message ID
        idempotency_key = f"gmail_deposit:{message_id}"

        # Apply deposit to user's wallet
        await ledger_repo.apply_ledger_entry(
            db,
            user_id=user.id,
            kind="deposit_in",
            amount_cents=amount_cents,
            idempotency_key=idempotency_key,
            session_id=None,
            registration_id=None
        )

        await db.commit()

        logger.info(
            f"✅ Successfully deposited ${amount_cents/100:.2f} to user "
            f"{user.name} ({user.email}) - Message ID: {message_id}"
        )

        return True

    except Exception as e:
        logger.error(
            f"Error processing Interac deposit for message {message_id}: {str(e)}",
            exc_info=True
        )
        await db.rollback()
        return False
