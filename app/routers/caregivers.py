import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db

router = APIRouter(prefix="/caregivers", tags=["caregivers"])


class UpdateCaregiver(BaseModel):
    phone_number: str


@router.patch("/{caregiver_id}")
async def update_caregiver(caregiver_id: int, body: UpdateCaregiver):
    try:
        row = await db.pool().fetchrow(
            "UPDATE caregivers SET phone_number = $1 WHERE id = $2 RETURNING id, name, phone_number",
            body.phone_number,
            caregiver_id,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="phone number already in use by another caregiver")

    if row is None:
        raise HTTPException(status_code=404, detail="caregiver not found")
    return dict(row)
