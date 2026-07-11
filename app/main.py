from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db, redis_client
from app.config import settings
from app.routers import caregivers, shifts, webhook


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await redis_client.connect()
    yield
    await redis_client.disconnect()
    await db.disconnect()


app = FastAPI(title="ShiftConfirm", lifespan=lifespan)

app.include_router(shifts.router)
app.include_router(caregivers.router)
app.include_router(webhook.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/config")
async def get_config():
    """Safe-to-expose settings for the browser dashboard (no private keys)."""
    return {
        "vapi_public_key": settings.vapi_public_key,
        "vapi_assistant_primary_id": settings.vapi_assistant_primary_id,
        "vapi_assistant_referral_id": settings.vapi_assistant_referral_id,
        "phone_calling_enabled": bool(settings.vapi_phone_number_id),
    }


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
