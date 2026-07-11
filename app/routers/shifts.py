import json

from fastapi import APIRouter, HTTPException

from app import db, redis_client, vapi_client
from app.config import settings

router = APIRouter(prefix="/shifts", tags=["shifts"])


@router.get("")
async def list_shifts():
    rows = await db.pool().fetch(
        """
        SELECT
            s.id, s.status, s.shift_start, s.shift_end,
            s.client_name, s.client_address,
            s.original_caregiver_id, s.assigned_caregiver_id,
            oc.name AS original_caregiver_name,
            ac.name AS assigned_caregiver_name, ac.phone_number AS assigned_caregiver_phone
        FROM shifts s
        JOIN caregivers oc ON oc.id = s.original_caregiver_id
        JOIN caregivers ac ON ac.id = s.assigned_caregiver_id
        ORDER BY s.shift_start
        """
    )
    return [dict(row) for row in rows]


@router.post("/reset")
async def reset_demo_data():
    """
    Testing helper: restores every shift to 'scheduled' under its original caregiver,
    wipes referrals/call logs and any caregivers created via referrals, and clears
    the Redis cache. Wired to the dashboard's Reset button.
    """
    await db.pool().execute("DELETE FROM call_logs")
    await db.pool().execute("DELETE FROM referrals")
    await db.pool().execute(
        """
        UPDATE shifts
        SET assigned_caregiver_id = original_caregiver_id, status = 'scheduled', updated_at = now()
        """
    )
    await db.pool().execute(
        "DELETE FROM caregivers WHERE id NOT IN (SELECT original_caregiver_id FROM shifts)"
    )
    await redis_client.client().flushdb()
    return {"status": "reset"}


@router.get("/{shift_id}/status")
async def get_shift_status(shift_id: int):
    cached = await redis_client.client().get(redis_client.shift_status_key(shift_id))
    if cached is not None:
        return json.loads(cached)

    row = await db.pool().fetchrow("SELECT id, status FROM shifts WHERE id = $1", shift_id)
    if row is None:
        raise HTTPException(status_code=404, detail="shift not found")
    return {"shift_id": row["id"], "status": row["status"]}


@router.get("/{shift_id}/next-referral")
async def get_next_referral(shift_id: int):
    """
    Next unresolved referral candidate for this shift, if any — used by the browser
    dashboard to auto-chain into the next referral call once the current one ends.
    """
    row = await db.pool().fetchrow(
        """
        SELECT r.id AS referral_id, r.referred_name, r.referred_phone_number, r.message,
               s.shift_start, s.shift_end, s.client_name, s.client_address,
               rc.name AS referring_caregiver_name
        FROM referrals r
        JOIN shifts s ON s.id = r.shift_id
        JOIN caregivers rc ON rc.id = r.referring_caregiver_id
        WHERE r.shift_id = $1 AND r.accepted IS NULL
        ORDER BY r.sequence_number
        LIMIT 1
        """,
        shift_id,
    )
    return dict(row) if row else None


@router.post("/{shift_id}/call")
async def call_shift(shift_id: int):
    shift = await db.pool().fetchrow(
        """
        SELECT s.id, s.shift_start, s.shift_end, s.client_name, s.client_address, s.status,
               ac.id AS caregiver_id, ac.name AS caregiver_name, ac.phone_number AS caregiver_phone
        FROM shifts s
        JOIN caregivers ac ON ac.id = s.assigned_caregiver_id
        WHERE s.id = $1
        """,
        shift_id,
    )
    if shift is None:
        raise HTTPException(status_code=404, detail="shift not found")

    variable_values = {
        "caregiver_name": shift["caregiver_name"],
        "shift_id": str(shift["id"]),
        "shift_date": shift["shift_start"].strftime("%A, %B %-d"),
        "shift_start_time": shift["shift_start"].strftime("%-I:%M %p"),
        "shift_end_time": shift["shift_end"].strftime("%-I:%M %p"),
        "client_name": shift["client_name"],
        "client_address": shift["client_address"],
    }

    call = await vapi_client.create_call(
        assistant_id=settings.vapi_assistant_primary_id,
        phone_number=shift["caregiver_phone"],
        variable_values=variable_values,
    )
    vapi_call_id = call["id"]

    await db.pool().execute(
        """
        INSERT INTO call_logs (shift_id, referral_id, vapi_call_id, call_type, outcome)
        VALUES ($1, NULL, $2, 'primary', 'in_progress')
        """,
        shift_id,
        vapi_call_id,
    )
    await redis_client.client().set(
        redis_client.call_lookup_key(vapi_call_id),
        json.dumps({"shift_id": shift_id, "referral_id": None}),
    )
    await redis_client.client().set(
        redis_client.shift_status_key(shift_id),
        json.dumps({"shift_id": shift_id, "status": shift["status"]}),
    )

    return {"shift_id": shift_id, "vapi_call_id": vapi_call_id, "status": "call_placed"}
