# Birdie Buddies App (Backend skeleton)

## Quick start
```bash
cp .env.sample .env

docker compose up -d  # start Postgres & Redis

# Create a virtualenv and install deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the API
uvicorn app.main:app --reload
# GET http://localhost:8000/health

## Twilio SMS notifications
1. Populate `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` in `.env`.
2. Run the SMS worker alongside the API:
   ```bash
   python -m app.workers.sms_notifier
   ```
   The worker listens to session events and sends confirmation/waitlist texts to the host via Twilio.
