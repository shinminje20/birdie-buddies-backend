from redis import asyncio as aioredis
from .config import get_settings

_settings = get_settings()
redis = aioredis.from_url(_settings.REDIS_URL, encoding="utf-8", decode_responses=True)


async def redis_health() -> bool:
    try:
        pong = await redis.ping()
        return bool(pong)
    except Exception:
        return False
