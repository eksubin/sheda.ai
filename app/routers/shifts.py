import json

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import claude_client, db, redis_client, vapi_client
from app.config import settings
from app.shift_service import dial_referral_candidate, set_shift_status

router = APIRouter(prefix="/shifts", tags=["shifts"])


def _vapi_error_message(e: httpx.HTTPStatusError) -> str:
    try:
        body = e.response.json()
    except ValueError:
        return "Vapi rejected the call request."
    message = body.get("message", "Vapi rejected the call request.")
    return "; ".join(message) if isinstance(message, list) else str(message)


@router.get("")
async def list_shifts():
    rows = await db.pool().fetch(
        """
        SELECT
            s.id, s.status, s.shift_start, s.shift_end,
            s.client_name, s.client_address, s.is_oncall,
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

    try:
        call = await vapi_client.create_call(
            assistant_id=settings.vapi_assistant_primary_id,
            phone_number=shift["caregiver_phone"],
            variable_values=variable_values,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=_vapi_error_message(e)) from e
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


@router.get("/{shift_id}/candidates")
async def get_candidates(shift_id: int):
    """
    Other caregivers who could cover this shift: excludes the currently assigned
    caregiver and anyone already booked on a time-overlapping shift, ranked by a
    reliability score derived from that caregiver's past primary-call outcomes.
    """
    shift = await db.pool().fetchrow(
        "SELECT shift_start, shift_end, assigned_caregiver_id FROM shifts WHERE id = $1",
        shift_id,
    )
    if shift is None:
        raise HTTPException(status_code=404, detail="shift not found")

    rows = await db.pool().fetch(
        """
        SELECT
            c.id, c.name, c.phone_number,
            COALESCE(stats.total, 0) AS calls_total,
            COALESCE(stats.confirmed, 0) AS calls_confirmed
        FROM caregivers c
        LEFT JOIN (
            SELECT s.original_caregiver_id AS caregiver_id,
                   count(*) AS total,
                   count(*) FILTER (WHERE cl.outcome = 'confirmed') AS confirmed
            FROM call_logs cl
            JOIN shifts s ON s.id = cl.shift_id
            WHERE cl.call_type = 'primary'
            GROUP BY s.original_caregiver_id
        ) stats ON stats.caregiver_id = c.id
        WHERE c.id != $2
        AND NOT EXISTS (
            SELECT 1 FROM shifts s2
            WHERE s2.assigned_caregiver_id = c.id
            AND s2.status NOT IN ('declined', 'needs_human')
            AND s2.shift_start < $1::timestamptz
            AND s2.shift_end > $3::timestamptz
        )
        ORDER BY c.name
        """,
        shift["shift_end"],
        shift["assigned_caregiver_id"],
        shift["shift_start"],
    )

    candidates = []
    for row in rows:
        total, confirmed = row["calls_total"], row["calls_confirmed"]
        match_score = round((confirmed / total) * 100) if total > 0 else 75
        candidates.append(
            {
                "caregiver_id": row["id"],
                "name": row["name"],
                "phone_number": row["phone_number"],
                "match_score": match_score,
                "calls_completed": total,
            }
        )
    candidates.sort(key=lambda c: c["match_score"], reverse=True)
    return candidates


class RequestCoverageBody(BaseModel):
    caregiver_id: int


@router.post("/{shift_id}/request-coverage")
async def request_coverage(shift_id: int, body: RequestCoverageBody):
    """
    Staff-initiated equivalent of a caregiver naming a referral mid-call: asks a
    specific caregiver to cover this shift and immediately places a real call to them.
    """
    shift = await db.pool().fetchrow(
        "SELECT assigned_caregiver_id, status FROM shifts WHERE id = $1", shift_id
    )
    if shift is None:
        raise HTTPException(status_code=404, detail="shift not found")

    candidate = await db.pool().fetchrow(
        "SELECT id, name, phone_number FROM caregivers WHERE id = $1", body.caregiver_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="caregiver not found")

    next_sequence = await db.pool().fetchval(
        "SELECT COALESCE(MAX(sequence_number), 0) + 1 FROM referrals WHERE shift_id = $1",
        shift_id,
    )

    referral = await db.pool().fetchrow(
        """
        INSERT INTO referrals
            (shift_id, referring_caregiver_id, referred_name, referred_phone_number, sequence_number)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, referred_name, referred_phone_number, message
        """,
        shift_id,
        shift["assigned_caregiver_id"],
        candidate["name"],
        candidate["phone_number"],
        next_sequence,
    )

    await set_shift_status(shift_id, "pending_referral")

    call_placed = False
    call_error = None
    if settings.vapi_phone_number_id:
        try:
            await dial_referral_candidate(shift_id, dict(referral))
            call_placed = True
        except httpx.HTTPStatusError as e:
            # referral is recorded either way; the call itself failed (e.g. bad/test phone number)
            call_error = _vapi_error_message(e)

    return {
        "referral_id": referral["id"],
        "status": "pending_referral",
        "call_placed": call_placed,
        "call_error": call_error,
    }


@router.get("/{shift_id}/activity")
async def get_activity(shift_id: int):
    """Read-only timeline synthesized from call_logs + referrals, for the AI panel feed."""
    shift = await db.pool().fetchrow(
        """
        SELECT ac.name AS assigned_caregiver_name
        FROM shifts s
        JOIN caregivers ac ON ac.id = s.assigned_caregiver_id
        WHERE s.id = $1
        """,
        shift_id,
    )
    if shift is None:
        raise HTTPException(status_code=404, detail="shift not found")

    call_rows = await db.pool().fetch(
        """
        SELECT cl.call_type, cl.outcome, cl.started_at, cl.ended_at, r.referred_name
        FROM call_logs cl
        LEFT JOIN referrals r ON r.id = cl.referral_id
        WHERE cl.shift_id = $1
        ORDER BY cl.started_at
        """,
        shift_id,
    )
    referral_rows = await db.pool().fetch(
        "SELECT referred_name, created_at, accepted FROM referrals WHERE shift_id = $1 ORDER BY created_at",
        shift_id,
    )

    events = []
    for row in call_rows:
        who = row["referred_name"] or shift["assigned_caregiver_name"]
        kind = "referral call" if row["call_type"] == "referral" else "call"
        text = f"{kind.capitalize()} to {who}"
        text += f" — {row['outcome'].replace('_', ' ')}" if row["ended_at"] else " — in progress"
        events.append({"type": "call", "text": text, "at": row["started_at"].isoformat()})

    for row in referral_rows:
        text = f"Requested coverage from {row['referred_name']}"
        if row["accepted"] is True:
            text = f"{row['referred_name']} accepted coverage"
        elif row["accepted"] is False:
            text = f"{row['referred_name']} declined coverage"
        events.append({"type": "referral", "text": text, "at": row["created_at"].isoformat()})

    events.sort(key=lambda e: e["at"])
    return events


@router.get("/{shift_id}/messages")
async def get_messages(shift_id: int):
    rows = await db.pool().fetch(
        "SELECT id, role, content, created_at FROM shift_messages WHERE shift_id = $1 ORDER BY created_at",
        shift_id,
    )
    return [dict(row) for row in rows]


class ChatBody(BaseModel):
    message: str


@router.post("/{shift_id}/chat")
async def chat(shift_id: int, body: ChatBody):
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="AI chat isn't configured — set ANTHROPIC_API_KEY.")

    shift = await db.pool().fetchrow(
        """
        SELECT s.status, s.shift_start, s.shift_end, s.client_name, s.client_address,
               ac.name AS assigned_caregiver_name
        FROM shifts s
        JOIN caregivers ac ON ac.id = s.assigned_caregiver_id
        WHERE s.id = $1
        """,
        shift_id,
    )
    if shift is None:
        raise HTTPException(status_code=404, detail="shift not found")

    activity = await get_activity(shift_id)
    activity_lines = "\n".join(f"- {e['text']}" for e in activity) or "- No calls or referrals yet."

    system_prompt = (
        "You are a scheduling copilot helping a home-care staff member with one specific shift. "
        "Be concise and practical — a sentence or two unless more detail is clearly needed.\n\n"
        f"Shift status: {shift['status']}\n"
        f"Assigned caregiver: {shift['assigned_caregiver_name']}\n"
        f"Client: {shift['client_name']} at {shift['client_address']}\n"
        f"Time: {shift['shift_start']} to {shift['shift_end']}\n\n"
        f"Recent activity:\n{activity_lines}"
    )

    await db.pool().execute(
        "INSERT INTO shift_messages (shift_id, role, content) VALUES ($1, 'user', $2)",
        shift_id,
        body.message,
    )

    reply = await claude_client.chat_reply(system_prompt, body.message)

    await db.pool().execute(
        "INSERT INTO shift_messages (shift_id, role, content) VALUES ($1, 'assistant', $2)",
        shift_id,
        reply,
    )

    return {"reply": reply}
