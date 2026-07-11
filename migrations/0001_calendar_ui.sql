-- One-off migration for the calendar UI rebuild.
-- init.sql only runs on empty volumes (see CLAUDE.md) — apply this by hand
-- against an already-running dev database:
--   docker compose exec -T postgres psql -U shiftconfirm -d shiftconfirm < migrations/0001_calendar_ui.sql

ALTER TABLE shifts ADD COLUMN IF NOT EXISTS is_oncall BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS shift_messages (
    id SERIAL PRIMARY KEY,
    shift_id INTEGER NOT NULL REFERENCES shifts(id),
    role TEXT NOT NULL, -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shift_messages_shift_id ON shift_messages(shift_id);
