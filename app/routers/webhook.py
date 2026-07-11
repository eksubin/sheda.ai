import json

from fastapi import APIRouter, Request

from app import db, redis_client, vapi_client
from app.config import settings

router = APIRouter(prefix="/vapi", tags=["webhook"])


async def _lookup_call(vapi_call_id: str) -> dict | None:
    cached = await redis_client.client().get(redis_client.call_lookup_key(vapi_call_id))
    if cached is not None:
        return json.loads(cached)
    row = await db.pool().fetchrow(
        "SELECT shift_id, referral_id FROM call_logs WHERE vapi_call_id = $1", vapi_call_id
    )
    return dict(row) if row else None


def _extract_variable(call: dict, key: str):
    return (call.get("assistantOverrides") or {}).get("variableValues", {}).get(key)


async def _set_shift_status(shift_id: int, status: str) -> None:
    await db.pool().execute(
        "UPDATE shifts SET status = $1, updated_at = now() WHERE id = $2", status, shift_id
    )
    await redis_client.client().set(
        redis_client.shift_status_key(shift_id),
        json.dumps({"shift_id": shift_id, "status": status}),
    )


async def _handle_lookup_shift(arguments: dict, call: dict) -> str:
    caregiver_name = arguments["caregiver_name"]

    row = await db.pool().fetchrow(
        """
        SELECT s.id AS shift_id, s.shift_start, s.shift_end, s.client_name, s.client_address
        FROM shifts s
        JOIN caregivers c ON c.id = s.assigned_caregiver_id
        WHERE c.name ILIKE $1 AND s.status IN ('scheduled', 'no_answer')
        ORDER BY s.shift_start
        LIMIT 1
        """,
        f"%{caregiver_name}%",
    )
    if row is None:
        return f"I couldn't find an upcoming shift for {caregiver_name}. I'll have a human scheduler follow up instead."

    vapi_call_id = call["id"]
    await db.pool().execute(
        """
        INSERT INTO call_logs (shift_id, referral_id, vapi_call_id, call_type, outcome)
        VALUES ($1, NULL, $2, 'primary', 'in_progress')
        ON CONFLICT (vapi_call_id) DO NOTHING
        """,
        row["shift_id"],
        vapi_call_id,
    )
    await redis_client.client().set(
        redis_client.call_lookup_key(vapi_call_id),
        json.dumps({"shift_id": row["shift_id"], "referral_id": None}),
    )

    return (
        f"Found it — {caregiver_name} is scheduled with {row['client_name']} at {row['client_address']} on "
        f"{row['shift_start'].strftime('%A, %B %-d')} from {row['shift_start'].strftime('%-I:%M %p')} "
        f"to {row['shift_end'].strftime('%-I:%M %p')}."
    )


async def _handle_confirm_shift(arguments: dict, call: dict, lookup: dict) -> str:
    shift_id = lookup["shift_id"]
    await _set_shift_status(shift_id, "confirmed")
    await db.pool().execute(
        "UPDATE call_logs SET outcome = 'confirmed' WHERE vapi_call_id = $1", call["id"]
    )
    return "Great, thanks for confirming — you're all set for the shift."


async def _handle_decline_shift(arguments: dict, call: dict, lookup: dict) -> str:
    shift_id = lookup["shift_id"]
    await _set_shift_status(shift_id, "needs_human")
    await db.pool().execute(
        "UPDATE call_logs SET outcome = 'declined' WHERE vapi_call_id = $1", call["id"]
    )
    return "No problem, thanks for letting us know. I'll have a human scheduler follow up to find coverage."


async def _dial_referral_candidate(shift_id: int, referral: dict) -> None:
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


async def _next_unresolved_referral(shift_id: int) -> dict | None:
    row = await db.pool().fetchrow(
        """
        SELECT id, referred_name, referred_phone_number, message
        FROM referrals
        WHERE shift_id = $1 AND accepted IS NULL
        ORDER BY sequence_number
        LIMIT 1
        """,
        shift_id,
    )
    return dict(row) if row else None


async def _find_caregiver_by_name(name: str) -> dict | None:
    row = await db.pool().fetchrow(
        "SELECT id, name, phone_number FROM caregivers WHERE name ILIKE $1 LIMIT 1",
        f"%{name}%",
    )
    return dict(row) if row else None


async def _handle_lookup_coworker(arguments: dict, call: dict) -> str:
    name = arguments["name"]
    coworker = await _find_caregiver_by_name(name)
    if coworker is None:
        return (
            f"I don't have anyone named {name} in the system. "
            "Ask the caller for that person's phone number so we can still reach out."
        )
    return (
        f"Found {coworker['name']} in the system — we have their contact details on file, "
        "no phone number needed. Confirm with the caller that this is who they mean."
    )


async def _handle_refer_replacement(arguments: dict, call: dict, lookup: dict) -> str:
    shift_id = lookup["shift_id"]
    candidates = arguments["candidates"][:3]
    message = arguments.get("message_for_candidates")

    referring_caregiver_id = await db.pool().fetchval(
        "SELECT assigned_caregiver_id FROM shifts WHERE id = $1", shift_id
    )

    resolved = []
    unresolved_names = []
    for candidate in candidates:
        phone = candidate.get("phone_number")
        name = candidate["name"]
        if not phone:
            coworker = await _find_caregiver_by_name(name)
            if coworker is None:
                unresolved_names.append(name)
                continue
            name, phone = coworker["name"], coworker["phone_number"]
        resolved.append({"name": name, "phone_number": phone})

    if not resolved:
        names = ", ".join(unresolved_names)
        return (
            f"I couldn't find {names} in the system. "
            "Ask the caller for a phone number for each of them, then call refer_replacement again."
        )

    for i, candidate in enumerate(resolved, start=1):
        await db.pool().execute(
            """
            INSERT INTO referrals
                (shift_id, referring_caregiver_id, referred_name, referred_phone_number, sequence_number, message)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            shift_id,
            referring_caregiver_id,
            candidate["name"],
            candidate["phone_number"],
            i,
            message,
        )

    await db.pool().execute(
        "UPDATE call_logs SET outcome = 'referred' WHERE vapi_call_id = $1", call["id"]
    )
    await _set_shift_status(shift_id, "pending_referral")

    if settings.vapi_phone_number_id:
        first_candidate = await _next_unresolved_referral(shift_id)
        await _dial_referral_candidate(shift_id, first_candidate)

    reply = "Got it, thanks. I'll check with them and call you back once I hear from someone."
    if unresolved_names:
        names = ", ".join(unresolved_names)
        reply += (
            f" Note: I couldn't find {names} in the system, so they were skipped — "
            "ask the caller for a phone number for them if they still want them included."
        )
    return reply


async def _handle_confirm_replacement(arguments: dict, call: dict, lookup: dict) -> str:
    shift_id = lookup["shift_id"]
    referral_id = lookup["referral_id"]
    accepted = arguments.get("accepted", False)

    referral = await db.pool().fetchrow(
        "SELECT referred_name, referred_phone_number FROM referrals WHERE id = $1", referral_id
    )

    await db.pool().execute("UPDATE referrals SET accepted = $1 WHERE id = $2", accepted, referral_id)
    await db.pool().execute(
        "UPDATE call_logs SET outcome = $1 WHERE vapi_call_id = $2",
        "confirmed" if accepted else "declined",
        call["id"],
    )

    if accepted:
        caregiver_id = await db.pool().fetchval(
            """
            INSERT INTO caregivers (name, phone_number)
            VALUES ($1, $2)
            ON CONFLICT (phone_number) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            referral["referred_name"],
            referral["referred_phone_number"],
        )
        await db.pool().execute(
            "UPDATE shifts SET assigned_caregiver_id = $1, updated_at = now() WHERE id = $2",
            caregiver_id,
            shift_id,
        )
        # close out any other still-unresolved candidates for this shift now that it's filled
        await db.pool().execute(
            "UPDATE referrals SET accepted = false WHERE shift_id = $1 AND accepted IS NULL",
            shift_id,
        )
        await _set_shift_status(shift_id, "reassigned")
        return "Perfect, you're now confirmed for that shift. Thanks so much!"

    next_candidate = await _next_unresolved_referral(shift_id)
    if next_candidate is None:
        await _set_shift_status(shift_id, "needs_human")
    elif settings.vapi_phone_number_id:
        await _dial_referral_candidate(shift_id, next_candidate)

    return "No problem, thanks for letting us know. We'll check with someone else."


TOOL_HANDLERS = {
    "confirm_shift": _handle_confirm_shift,
    "decline_shift": _handle_decline_shift,
    "refer_replacement": _handle_refer_replacement,
    "confirm_replacement": _handle_confirm_replacement,
}

ENDED_REASON_TO_OUTCOME = {
    "customer-did-not-answer": "no_answer",
    "customer-busy": "no_answer",
    "voicemail": "voicemail",
}


async def _bootstrap_lookup_from_variables(call: dict) -> dict | None:
    """
    Establishes the call_id -> shift association for calls we didn't place ourselves
    (browser-initiated calls, e.g. from the dashboard's Call button or an auto-chained
    referral call), which never went through vapi_client.create_call's own bookkeeping.
    Relies on shift_id/referral_id already being passed in as variableValues.
    """
    shift_id = _extract_variable(call, "shift_id")
    if shift_id is None:
        return None

    referral_id = _extract_variable(call, "referral_id")
    lookup = {"shift_id": int(shift_id), "referral_id": int(referral_id) if referral_id else None}

    await db.pool().execute(
        """
        INSERT INTO call_logs (shift_id, referral_id, vapi_call_id, call_type, outcome)
        VALUES ($1, $2, $3, $4, 'in_progress')
        ON CONFLICT (vapi_call_id) DO NOTHING
        """,
        lookup["shift_id"],
        lookup["referral_id"],
        call["id"],
        "referral" if lookup["referral_id"] else "primary",
    )
    await redis_client.client().set(redis_client.call_lookup_key(call["id"]), json.dumps(lookup))
    return lookup


async def _handle_tool_calls(message: dict) -> dict:
    call = message["call"]
    vapi_call_id = call["id"]
    lookup = await _lookup_call(vapi_call_id) or await _bootstrap_lookup_from_variables(call)

    results = []
    for tool_call in message.get("toolCallList", []):
        function = tool_call["function"]
        name = function["name"]
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        if name == "lookup_shift":
            result = await _handle_lookup_shift(arguments, call)
            lookup = await _lookup_call(vapi_call_id)
        elif name == "lookup_coworker":
            result = await _handle_lookup_coworker(arguments, call)
        else:
            handler = TOOL_HANDLERS.get(name)
            if handler is None or lookup is None:
                result = "Sorry, I wasn't able to process that."
            else:
                result = await handler(arguments, call, lookup)

        results.append({"toolCallId": tool_call["id"], "result": result})

    return {"results": results}


async def _handle_end_of_call_report(message: dict) -> dict:
    call = message["call"]
    vapi_call_id = call["id"]
    lookup = await _lookup_call(vapi_call_id)
    ended_reason = message.get("endedReason")
    transcript = message.get("transcript")

    row = await db.pool().fetchrow(
        "SELECT outcome FROM call_logs WHERE vapi_call_id = $1", vapi_call_id
    )
    outcome = row["outcome"] if row else "in_progress"
    if outcome == "in_progress":
        outcome = ENDED_REASON_TO_OUTCOME.get(ended_reason, outcome)

    await db.pool().execute(
        """
        UPDATE call_logs
        SET transcript = $1, ended_reason = $2, ended_at = now(), outcome = $3
        WHERE vapi_call_id = $4
        """,
        transcript,
        ended_reason,
        outcome,
        vapi_call_id,
    )

    if lookup and outcome in ("no_answer", "voicemail"):
        await _set_shift_status(lookup["shift_id"], "no_answer")

    return {"status": "ok"}


@router.post("/webhook")
async def vapi_webhook(request: Request):
    body = await request.json()
    message = body.get("message", {})
    message_type = message.get("type")

    if message_type == "tool-calls":
        return await _handle_tool_calls(message)
    if message_type == "end-of-call-report":
        return await _handle_end_of_call_report(message)

    return {"status": "ignored"}
