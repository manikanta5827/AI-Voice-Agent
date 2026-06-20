-- Tracks each inbound/outbound call
CREATE TABLE IF NOT EXISTS calls (
    call_sid   TEXT PRIMARY KEY,
    stream_sid TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at   TIMESTAMPTZ
);

-- Stores every user + assistant utterance for a call
CREATE TABLE IF NOT EXISTS messages (
    id         BIGSERIAL PRIMARY KEY,
    call_sid   TEXT        NOT NULL REFERENCES calls(call_sid),
    role       TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
