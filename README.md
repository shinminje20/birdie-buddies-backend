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
