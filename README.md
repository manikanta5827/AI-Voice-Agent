# AI Voice Agent — SecureLife Insurance

Telugu-language voice agent for SecureLife Insurance. Built with Pipecat, Soniox STT, Cartesia TTS, OpenAI GPT-4.1-mini, and Twilio Media Streams.

## Stack

| Layer | Service |
|-------|---------|
| Telephony | Twilio (Media Streams via WebSocket) |
| STT | Soniox (`stt-rt-v5`, Telugu) |
| LLM | OpenAI GPT-4.1-mini |
| TTS | Cartesia Sonic-2 (Bavani voice) |
| Orchestration | Pipecat |
| DB | Supabase PostgreSQL (raw `asyncpg`) |

## Setup

### 1. Prerequisites

- Python 3.11+
- [ngrok](https://ngrok.com) for local dev tunnel

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Fill in `.env`:

```env
PUBLIC_URL=your-ngrok-subdomain.ngrok-free.app   # no https://

TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_US_NUMBER=+1...
MY_INDIAN_NUMBER=+91...

SONIOX_API_KEY=...

CARTESIA_API_KEY=...
CARTESIA_VOICE_ID=...   # Bavani voice UUID from cartesia.ai/voices

OPENAI_API_KEY=...

PORT=8080
MAX_CALL_MINUTES=3

DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[ref].supabase.co:5432/postgres
```

### 4. Database

Run in Supabase SQL editor (skip if tables already exist):

```sql
CREATE TABLE IF NOT EXISTS calls (
  id          SERIAL PRIMARY KEY,
  call_sid    TEXT UNIQUE NOT NULL,
  stream_sid  TEXT,
  started_at  TIMESTAMPTZ,
  ended_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS messages (
  id         SERIAL PRIMARY KEY,
  call_sid   TEXT NOT NULL,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ
);
```

### 5. Start the server

```bash
python server.py
```

Dev mode with auto-reload:

```bash
uvicorn server:app --reload --port 8080
```

### 6. Expose via ngrok

```bash
ngrok http 8080
```

Copy the subdomain (e.g. `abc-123.ngrok-free.app`) → set as `PUBLIC_URL` in `.env`.

### 7. Configure Twilio webhook

Set your Twilio phone number's webhook URL to:
```
https://YOUR_NGROK_URL/incoming-call   (HTTP POST)
```

### 8. Trigger a call

```bash
curl http://localhost:8080/make-call
```

## File structure

```
server.py          FastAPI entry — Twilio webhooks + WebSocket handshake
bot.py             Pipecat pipeline — STT → LLM → TTS, idle/end-call logic
services/
  stt.py           Soniox STT (Telugu, stt-rt-v5)
  tts.py           Cartesia TTS (Sonic-2, Bavani voice)
  llm.py           OpenAI LLM + system prompt (priya persona)
db.py              Supabase PostgreSQL — call and message logging
requirements.txt
.env.example
```

## Database: why no ORM?

Two tables, four queries. Raw `asyncpg` (PostgreSQL async driver) is the right fit — no abstractions needed.

If you later add more tables or complex joins, [SQLAlchemy 2.x async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) is the Python standard ORM. Add it then, not now.

## Features

- Barge-in: user can interrupt agent mid-response (Silero VAD + Pipecat interruptions)
- Idle detection: 5s warning → 10s hangup
- Max call duration: `MAX_CALL_MINUTES` env var (default 3 min), LLM generates farewell
- End-call on goodbye signals (`bye`, `చాలు`, `అయిపోయింది`, etc.)
- All turns logged to Supabase
