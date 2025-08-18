from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: Literal["dev", "prod", "test"] = "dev"
    DEBUG: bool = True

    # App
    APP_NAME: str = "badminton-app"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # Database
    DATABASE_URL: str  # async driver for runtime (e.g., postgresql+asyncpg://user:pass@host/db)
    SYNC_DATABASE_URL: str | None = None  # sync driver for Alembic (e.g., postgresql+psycopg://...)

    # Redis
    REDIS_URL: str  # e.g., redis://localhost:6379/0
    
    # Auth
    JWT_SECRET: str = "dev-secret-change-me"  # set a strong random value in prod
    JWT_EXPIRE_MINUTES: int = 60 * 30      # 30 minutes
    SESSION_COOKIE_NAME: str = "session"
    
    # Rate limits (sane defaults for dev)
    RL_REG_PER_USER_10S: int = 5      # /sessions/{id}/register per user per 10s
    RL_REG_PER_IP_10S: int = 30       # ... per IP per 10s
    RL_OTP_REQ_PER_IP_10S: int = 5    # /auth/request-otp per IP per 10s
    RL_OTP_VERIFY_PER_IP_10S: int = 10

    # Registration backlog cap (number of unprocessed messages allowed)
    REGISTRATION_QUEUE_MAX: int = 120
    
    # Logging / Observability
    LOG_LEVEL: str = "INFO"
    SLOW_QUERY_MS: int = 300          # warn if a DB query exceeds this
    METRICS_ENABLED: bool = True
    REQUEST_ID_HEADER: str = "X-Request-ID"
    
    FRONTEND_ORIGIN: str = "http://localhost:5173"  # vite dev server
    FRONTEND_DEPLOYED_domain_1: str = "birdie-buddies-a32af.web.app"
    FRONTEND_DEPLOYED_domain_2: str = "birdie-buddies-a32af.firebaseapp.com"
    AUTO_CLOSE_INTERVAL_SEC: int = 30      # how often the worker scans
    AUTO_CLOSE_BATCH: int = 200            # max sessions to close per scan
    AUTO_CLOSE_LOCK_TTL_SEC: int = 25      # Redis lock TTL (must be < interval)
    
    @field_validator("SYNC_DATABASE_URL", mode="before")
    @classmethod
    def default_sync_if_missing(cls, v, values):
        if v:
            return v
        url = values.get("DATABASE_URL")
        # Replace asyncpg with psycopg for Alembic usage if possible
        if url and url.startswith("postgresql+asyncpg://"):
            return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        return v


def get_settings() -> Settings:
    # slightly faster singleton
    global _SETTINGS_SINGLETON
    try:
        return _SETTINGS_SINGLETON  # type: ignore[name-defined]
    except NameError:
        _SETTINGS_SINGLETON = Settings()  # type: ignore[assignment]
        return _SETTINGS_SINGLETON
