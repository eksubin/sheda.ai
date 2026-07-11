# ShiftConfirm

Voice-AI caregiver scheduling assistant — built for the Arya Health Hack. Automatically calls caregivers to confirm upcoming shifts, and if they can't make it, calls a referred friend to cover it instead.

See [CLAUDE.md](CLAUDE.md) for full architecture, flow, and Vapi integration notes.

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
5. Start everything:
   ```bash
   docker compose up --build
   ```

API: `http://localhost:8000` (docs at `/docs`). Dashboard: `http://localhost:8000/`.

Postgres is seeded with 2 demo caregivers and 1 demo shift on first boot (`init.sql`).

## Triggering a call

From the dashboard, click "Call" next to a scheduled shift — or directly:

```bash
curl -X POST http://localhost:8000/shifts/1/call
```

Then watch `GET /shifts/1/status` (or the dashboard) update as the call progresses.
