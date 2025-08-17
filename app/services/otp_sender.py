from __future__ import annotations

# DEV sender: print the OTP. Replace with SES/Postmark/Twilio later.

async def send_otp_via_email(email: str, code: str) -> None:
    # In real life, send an email. For now, just log/print.
    print(f"[DEV] OTP for {email}: {code}")
