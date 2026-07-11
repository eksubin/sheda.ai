# CLAUDE.md

This file gives Claude Code the context it needs to work in this repo. Read this before making changes.

## What this project is

ShiftConfirm — a voice-AI caregiver scheduling assistant built for the Arya Health Hack (luma.com/arya-health-hack). It automatically calls caregivers to confirm upcoming shifts, and if a caregiver can't make it, it can call a friend/colleague they've referred to cover the shift instead — a fully automated hand-off chain.

Why this shape: the hackathon's sponsor, Arya Health, builds AI agents for post-acute/home-care administration with multimodal (voice/text/email) communication that reads/writes to an EMR-like system of record. This project is a narrow, demoable slice of that: caregiver scheduling confirmation over real phone calls.

## Tech stack

* FastAPI (async) — API + webhook receiver
* PostgreSQL (asyncpg, raw SQL — no ORM) — source of truth: caregivers, shifts, referrals, call_logs
* Redis — fast call-state cache for dashboard polling (`shift_status:{id}` keys) and call_id → shift_id / referral_id lookups so incoming Vapi webhooks can be resolved quickly
* Vapi — voice AI orchestration layer (handles STT → LLM → TTS + turn-taking); we don't touch raw speech APIs directly
* Twilio — underlying telephony; a Twilio number is imported into Vapi (Vapi dashboard → Phone Numbers → Import), so Twilio is not called directly from this codebase — Vapi owns the call once the number is imported
* Docker Compose — api + postgres + redis, one command to run

## Core flow

1. Dashboard/API triggers `POST /shifts/{id}/call` → FastAPI calls Vapi's `/call` API → Vapi places an outbound call via the imported Twilio number to the assigned caregiver, running the primary assistant.
2. During the call, the assistant either:
   * Confirms → calls tool `confirm_shift` → webhook updates `shifts.status = 'confirmed'`
   * Can't make it + names a friend with a phone number → calls tool `refer_replacement` → webhook creates a `referrals` row, sets `shifts.status = 'pending_referral'`, and immediately triggers a second outbound call to the friend using the referral assistant
   * Can't make it, no referral → status becomes `needs_human`, no further automation (by design — don't chain referrals infinitely for the hackathon demo)
3. The referral call runs, and the friend either:
   * Accepts → tool `confirm_replacement(accepted=true)` → upserts a new `caregivers` row for the friend if needed, reassigns `shifts.assigned_caregiver_id`, sets status `reassigned`
   * Declines → status `needs_human`
4. `end-of-call-report` webhook events always fire at call end regardless of outcome — used to store transcript + `ended_reason` in `call_logs`, and to catch no-answer/voicemail cases that never triggered a tool call.

Redis is written to at every status transition specifically so a live dashboard can poll `GET /shifts/{id}/status` fast during a demo without hitting Postgres each time.

## Vapi integration specifics (important — don't relitigate these)

* Two separate Vapi assistants, not one: `assistant_primary.json` (talks to the originally-scheduled caregiver) and `assistant_referral.json` (talks to the referred friend). Different first message, different system prompt, different tone justification ("cold-ish call, so be extra clear who you are"). This was a deliberate design choice for both correctness and demo narrative ("two specialized agents handing off a task").
* Tool definitions live in `vapi_config/tools.json`. All three tools (`confirm_shift`, `refer_replacement`, `confirm_replacement`) point their `server.url` at `{WEBHOOK_BASE_URL}/vapi/webhook` — same single webhook endpoint, dispatched internally by tool name (see `app/routers/webhook.py::TOOL_HANDLERS`).
* Context (caregiver name, shift time, shift ID, etc.) is passed into each call via `assistantOverrides.variableValues` at call-creation time, referenced in prompts as `{{variableName}}`. The webhook handler reads these back out of the incoming `message.call.assistantOverrides.variableValues` when it needs them (see `_extract_variable` in `webhook.py`) — there is no separate lookup call back to Vapi for this.
* Phone number setup: import a Twilio number into Vapi (dashboard → Phone Numbers → Import, using Twilio Account SID + Auth Token), not raw SIP trunking — SIP trunking is unnecessary complexity for a hackathon timeline.
* Local dev: run `ngrok http 8000`, set that URL as `WEBHOOK_BASE_URL` in `.env`, and also update `server.url` in `vapi_config/tools.json` before creating/updating assistants via the Vapi API or dashboard.
* Vapi webhook payloads to handle: `tool-calls` (respond with `{"results": [{"toolCallId", "result"}]}` — result is a string spoken/used by the assistant) and `end-of-call-report` (fire-and-forget logging, no response body needed beyond `{"status": "ok"}`).

## Database

Schema lives in `init.sql`, auto-applied by the `postgres` container on first boot via the Compose volume mount. Key tables: `caregivers`, `shifts` (has both `assigned_caregiver_id` — current owner, mutable — and `original_caregiver_id` — fixed, for audit trail), `referrals` (the hand-off chain), `call_logs` (transcript + outcome per call, keyed by `vapi_call_id`).

`shift_status` enum: `scheduled, confirmed, declined, pending_referral, reassigned, no_answer, needs_human`
`call_outcome` enum: `confirmed, declined, referred, no_answer, voicemail, in_progress`

If you add migrations later, don't touch `init.sql` retroactively for a running dev DB — write a proper migration (e.g. add Alembic) since `init.sql` only runs on empty volumes.

## Explicit non-goals for the hackathon build

* No retry queues or background workers (Celery/RQ) — calls are triggered synchronously from a request handler. Fine for demo volume.
* No webhook signature verification — skipped for time; flag this if extending past the hackathon.
* No infinite referral chains — one hop (caregiver → friend) only. A friend declining goes straight to `needs_human`, not a third call.
* No auth on the API — add before any real deployment.

## Running it

```bash
cp .env.example .env   # fill in Vapi keys + IDs
docker compose up --build
```

API on `http://localhost:8000`, docs at `/docs`. Postgres seeded with 2 demo caregivers + 1 demo shift via `init.sql`.

## What's NOT built yet (likely next steps)

* Frontend dashboard (React/Next, whatever's fastest) consuming `GET /shifts`, `GET /shifts/{id}/status`, `POST /shifts/{id}/call`
* Script to actually register the two assistants + tools with the Vapi API from `vapi_config/*.json` (currently these are reference configs meant to be pasted into the Vapi dashboard or POSTed manually)
* Any retry/reminder logic for `no_answer` shifts
