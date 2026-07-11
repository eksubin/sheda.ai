# Sheda AI

Sheda AI is a voice-AI scheduling agent for nurse and home-care managers, built for the Arya Health Hack. It calls caregivers to confirm upcoming shifts, and if someone can't make it and names a coworker to cover, it automatically calls that person too — a live, automated hand-off chain. Everything lands in Postgres in real time and shows up on a calendar dashboard where a manager can also ask an AI copilot about any shift, or manually request coverage from a ranked list of available caregivers.

See [CLAUDE.md](CLAUDE.md) for full architecture, flow, and Vapi integration notes.

## What it does

- **Confirmation calls.** Trigger a real (or browser-test) call to a caregiver ahead of their shift; the call outcome updates the shift's status live.
- **Automated hand-off.** If a caregiver can't make it and names a friend with a phone number, a second call goes out immediately to that person — no dispatcher required.
- **Calendar dashboard.** A week-view calendar (day/evening/night/on-call color-coded) with live status, a candidate-matching panel for shifts that need coverage, and an AI chat panel for asking about any specific shift.
- **Full call/referral history**, transcripts, and outcomes stored per shift for audit.

## Setup

1. Create a Vapi account and import a Twilio number (Vapi dashboard → Phone Numbers → Import).
2. `cp .env.example .env` and fill in `VAPI_API_KEY` and `VAPI_PHONE_NUMBER_ID`.
3. In a separate terminal, run `ngrok http 8000` and copy the HTTPS forwarding URL into `WEBHOOK_BASE_URL` in `.env`.
4. Register the two assistants + tools with Vapi:
   ```bash
   pip install -r requirements.txt
   python scripts/register_assistants.py
   ```
   Copy the printed `VAPI_ASSISTANT_PRIMARY_ID` / `VAPI_ASSISTANT_REFERRAL_ID` into `.env`.
5. (Optional) Add an `ANTHROPIC_API_KEY` to `.env` to enable the AI chat panel on the dashboard.
6. Start everything:
   ```bash
   docker compose up --build
   ```

API: `http://localhost:8000` (docs at `/docs`). Dashboard: `http://localhost:8000/`.

Postgres is seeded with 5 demo caregivers and 5 demo shifts across a range of statuses on first boot (`init.sql`). If you're updating an existing (already-running) database rather than starting fresh, apply `migrations/0001_calendar_ui.sql` by hand — `init.sql` only runs on an empty volume.

## Triggering a call

Click a shift on the dashboard calendar, then **Call** (browser test call or, if `VAPI_PHONE_NUMBER_ID` is set, a real one) or **Call Phone** (always a real outbound call) in the popup — or directly:

```bash
curl -X POST http://localhost:8000/shifts/1/call
```

Then watch `GET /shifts/1/status` (or the dashboard) update as the call progresses.
