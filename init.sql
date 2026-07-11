-- ShiftConfirm schema
-- Applied automatically by the postgres container on first boot (empty volume only).
-- See CLAUDE.md: don't edit this retroactively for a running dev DB — write a migration instead.

CREATE TYPE shift_status AS ENUM (
    'scheduled',
    'confirmed',
    'declined',
    'pending_referral',
    'reassigned',
    'no_answer',
    'needs_human'
);

CREATE TYPE call_outcome AS ENUM (
    'confirmed',
    'declined',
    'referred',
    'no_answer',
    'voicemail',
    'in_progress'
);

CREATE TABLE caregivers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE shifts (
    id SERIAL PRIMARY KEY,
    original_caregiver_id INTEGER NOT NULL REFERENCES caregivers(id),
    assigned_caregiver_id INTEGER NOT NULL REFERENCES caregivers(id),
    shift_start TIMESTAMPTZ NOT NULL,
    shift_end TIMESTAMPTZ NOT NULL,
    client_name TEXT NOT NULL,
    client_address TEXT NOT NULL,
    status shift_status NOT NULL DEFAULT 'scheduled',
    is_oncall BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE referrals (
    id SERIAL PRIMARY KEY,
    shift_id INTEGER NOT NULL REFERENCES shifts(id),
    referring_caregiver_id INTEGER NOT NULL REFERENCES caregivers(id),
    referred_name TEXT NOT NULL,
    referred_phone_number TEXT NOT NULL,
    sequence_number INTEGER NOT NULL DEFAULT 1,
    message TEXT,
    accepted BOOLEAN,
    vapi_call_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE call_logs (
    id SERIAL PRIMARY KEY,
    shift_id INTEGER NOT NULL REFERENCES shifts(id),
    referral_id INTEGER REFERENCES referrals(id),
    vapi_call_id TEXT UNIQUE,
    call_type TEXT NOT NULL, -- 'primary' | 'referral'
    outcome call_outcome NOT NULL DEFAULT 'in_progress',
    ended_reason TEXT,
    transcript TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);

-- AI chat panel conversation log, one row per message, per shift.
CREATE TABLE shift_messages (
    id SERIAL PRIMARY KEY,
    shift_id INTEGER NOT NULL REFERENCES shifts(id),
    role TEXT NOT NULL, -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_shifts_status ON shifts(status);
CREATE INDEX idx_referrals_shift_id ON referrals(shift_id);
CREATE INDEX idx_call_logs_shift_id ON call_logs(shift_id);
CREATE INDEX idx_call_logs_vapi_call_id ON call_logs(vapi_call_id);
CREATE INDEX idx_shift_messages_shift_id ON shift_messages(shift_id);

-- Seed data: demo caregivers + demo shifts across a range of statuses
INSERT INTO caregivers (name, phone_number) VALUES
    ('Maria Gonzalez', '+15555550101'),
    ('James Whitfield', '+15555550102'),
    ('Aisha Patel', '+15555550103'),
    ('Devon Carter', '+15555550104'),
    ('Linda Nakamura', '+15555550105');

INSERT INTO shifts (original_caregiver_id, assigned_caregiver_id, shift_start, shift_end, client_name, client_address, status)
VALUES
    (1, 1, now() + interval '1 day' + interval '9 hours', now() + interval '1 day' + interval '13 hours',
     'Dorothy Ellis', '482 Birchwood Lane, Springfield', 'scheduled'),

    (2, 2, now() + interval '1 day' + interval '14 hours', now() + interval '1 day' + interval '18 hours',
     'Walter Higgins', '129 Maple Court, Springfield', 'scheduled'),

    (3, 3, now() + interval '2 days' + interval '8 hours', now() + interval '2 days' + interval '12 hours',
     'Evelyn Brooks', '77 Oakhurst Ave, Shelbyville', 'confirmed'),

    (4, 4, now() + interval '2 days' + interval '15 hours', now() + interval '2 days' + interval '19 hours',
     'Harold Simmons', '15 Riverside Dr, Shelbyville', 'no_answer'),

    (5, 5, now() + interval '3 days' + interval '9 hours', now() + interval '3 days' + interval '13 hours',
     'Grace Whitmore', '903 Cedar St, Capital City', 'needs_human');

UPDATE shifts SET is_oncall = true WHERE id = 3;
