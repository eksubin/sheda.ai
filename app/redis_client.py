import redis.asyncio as redis

from app.config import settings

_client: redis.Redis | None = None


async def connect() -> None:
    global _client
    _client = redis.from_url(settings.redis_url, decode_responses=True)


async def disconnect() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def client() -> redis.Redis:
    assert _client is not None, "redis client not initialized — call connect() first"
    return _client


def shift_status_key(shift_id: int) -> str:
    return f"shift_status:{shift_id}"


def call_lookup_key(vapi_call_id: str) -> str:
    return f"call_lookup:{vapi_call_id}"
