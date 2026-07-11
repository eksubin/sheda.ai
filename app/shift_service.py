import json

from app import db, redis_client, vapi_client
from app.config import settings


async def set_shift_status(shift_id: int, status: str) -> None:
    await db.pool().execute(
        "UPDATE shifts SET status = $1, updated_at = now() WHERE id = $2", status, shift_id
    )
    await redis_client.client().set(
        redis_client.shift_status_key(shift_id),
        json.dumps({"shift_id": shift_id, "status": status}),
    )


async def dial_referral_candidate(shift_id: int, referral: dict) -> None:
    """Places the actual outbound call to a referral candidate (phone mode only)."""
    shift = await db.pool().fetchrow(
        """
        SELECT s.shift_start, s.shift_end, s.client_name, s.client_address,
               ac.name AS referring_caregiver_name
        FROM shifts s
        JOIN caregivers ac ON ac.id = s.assigned_caregiver_id
        WHERE s.id = $1
        """,
        shift_id,
    )

    variable_values = {
        "referred_name": referral["referred_name"],
        "referring_caregiver_name": shift["referring_caregiver_name"],
        "shift_id": str(shift_id),
        "referral_id": str(referral["id"]),
        "caregiver_message": referral["message"] or "",
        "shift_date": shift["shift_start"].strftime("%A, %B %-d"),
        "shift_start_time": shift["shift_start"].strftime("%-I:%M %p"),
        "shift_end_time": shift["shift_end"].strftime("%-I:%M %p"),
        "client_name": shift["client_name"],
        "client_address": shift["client_address"],
    }
    referral_call = await vapi_client.create_call(
        assistant_id=settings.vapi_assistant_referral_id,
        phone_number=referral["referred_phone_number"],
        variable_values=variable_values,
    )
    referral_vapi_call_id = referral_call["id"]

    await db.pool().execute(
        """
        INSERT INTO call_logs (shift_id, referral_id, vapi_call_id, call_type, outcome)
        VALUES ($1, $2, $3, 'referral', 'in_progress')
        """,
        shift_id,
        referral["id"],
        referral_vapi_call_id,
    )
    await redis_client.client().set(
        redis_client.call_lookup_key(referral_vapi_call_id),
        json.dumps({"shift_id": shift_id, "referral_id": referral["id"]}),
    )
