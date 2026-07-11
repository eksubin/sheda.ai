import re

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/caregivers", tags=["caregivers"])


class UpdateCaregiver(BaseModel):
    phone_number: str


def _to_e164(raw: str) -> str:
    """Best-effort normalization to E.164, defaulting to +1 (US) when no country code is given."""
    raw = raw.strip()
    if raw.startswith("+"):
        return raw
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+1{digits}"


@router.patch("/{caregiver_id}")
async def update_caregiver(caregiver_id: int, body: UpdateCaregiver):
    phone_number = _to_e164(body.phone_number)
    try:
        row = await db.pool().fetchrow(
            "UPDATE caregivers SET phone_number = $1 WHERE id = $2 RETURNING id, name, phone_number",
            phone_number,
            caregiver_id,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="phone number already in use by another caregiver")

    if row is None:
        raise HTTPException(status_code=404, detail="caregiver not found")
    return dict(row)
