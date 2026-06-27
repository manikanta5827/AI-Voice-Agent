# AI Voice Agent — SecureLife Insurance

Telugu-language voice agent ("priya") for SecureLife Insurance. Built with Pipecat,
Soniox STT, Cartesia TTS, a configurable LLM, and **switchable telephony** —
Twilio or Vobiz, chosen by one environment variable.

## Stack

| Layer | Service |
|-------|---------|
| Telephony | **Twilio or Vobiz** (switch with `TELEPHONY`) — MULAW 8kHz over WebSocket |
| STT | Soniox (`stt-rt-v5`, Telugu) |
| LLM | Configurable via `LLM_PROVIDER`: gemini · deepseek · openai · groq · sarvam · ai_gateway |
| TTS | Cartesia Sonic-3.5 (Bavani voice), native 8kHz |
| Orchestration | Pipecat (STT → LLM → TTS) |
| DB | Supabase PostgreSQL (raw `asyncpg`) |

---

## Architecture

Telephony is provider-agnostic. The whole app speaks to **one adapter**,
`services/telephony.py`, which hides every Twilio/Vobiz difference. `server.py`
and `bot.py` never name a provider.

```
                       TELEPHONY=twilio | vobiz   (the only switch)
                                   │
        ┌──────────────────────────┴──────────────────────────┐
        │                  services/telephony.py               │
        │  provider()        → reads TELEPHONY                  │
        │  place_call()      → Twilio SDK   | Vobiz REST        │
        │  answer_xml()      → TwiML        | Vobiz Stream XML  │
        │  build_transport() → serializer + start-event parse  │
        └──────────────────────────┬──────────────────────────┘
                                    │
   server.py (3 standard routes, no provider logic)
        GET  /make-call       → place_call()        outbound dial
        */   /incoming-call   → answer_xml()        answer webhook
        WS   /media-stream    → run_bot()           audio stream
                                    │
   bot.py  run_bot(websocket)  → build_transport() → Pipecat pipeline
            transport.input → STT → EchoGate → LLM → MarkerStripper → TTS → transport.output
```

The **same three URLs serve both providers**. You never change routes to switch —
only the `TELEPHONY` value and that provider's credentials.

---

## Switching telephony providers

It's a single environment variable. No code change, no route change.

```env
# .env
TELEPHONY=twilio     # or: vobiz
```

`provider()` reads it **per request**, so changing `.env` + reloading the server
flips the entire stack — outbound dialing, the answer webhook XML, and the
media-stream handshake all follow.

| | Twilio | Vobiz |
|---|---|---|
| `TELEPHONY` | `twilio` | `vobiz` |
| Required creds | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_US_NUMBER` | `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_PHONE_NUMBER` |
| Point the number's webhook at | `https://PUBLIC_URL/incoming-call` | `https://PUBLIC_URL/incoming-call` |
| Outbound | `curl localhost:8080/make-call` | `curl localhost:8080/make-call` |
| Extra pip dep | (bundled) | `pipecat-vobiz` (in requirements.txt) |

> Both providers stream MULAW 8kHz, so the STT/LLM/TTS pipeline is identical —
> only the wire serializer and the start handshake differ, and both live in
> `telephony.py`.

---

## Setup

### 1. Prerequisites

- Python 3.11+
- [ngrok](https://ngrok.com) for a local dev tunnel

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Core values:

```env
PUBLIC_URL=your-ngrok-subdomain.ngrok-free.app   # bare host, no https://
TELEPHONY=twilio                                  # twilio | vobiz

# --- Twilio (if TELEPHONY=twilio) ---
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_US_NUMBER=+1...
MY_INDIAN_NUMBER=+91...        # outbound target

# --- Vobiz (if TELEPHONY=vobiz) ---
VOBIZ_AUTH_ID=...
VOBIZ_AUTH_TOKEN=...
VOBIZ_PHONE_NUMBER=+91...      # caller-ID for outbound
VOBIZ_ENCODING=audio/x-mulaw  # audio/x-mulaw | audio/x-l16
VOBIZ_SAMPLE_RATE=8000        # 8000 | 16000
VOBIZ_L16_ENDIAN=be           # flip to "le" only if x-l16 STT is silent

# --- AI services ---
SONIOX_API_KEY=...
CARTESIA_API_KEY=...
CARTESIA_VOICE_ID=...          # Bavani voice UUID from cartesia.ai/voices
LLM_PROVIDER=gemini            # gemini | deepseek | openai | groq | sarvam | ai_gateway
GEMINI_API_KEY=...             # (or the key matching your LLM_PROVIDER)

# --- Server ---
PORT=8080
MAX_CALL_MINUTES=3
TURN_SILENCE_SECS=0.3          # silence after speech before the turn ends
LLM_MAX_HISTORY=16             # context cap: system msg + last N messages
DEBUG_TTFB=                    # set to 1 to log per-turn latency timeline

DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[ref].supabase.co:5432/postgres
```

### 4. Database

Run in the Supabase SQL editor (skip if tables exist):

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

### 5. Run + expose

```bash
python server.py                       # or: uvicorn server:app --reload --port 8080
ngrok http 8080                        # copy the subdomain into PUBLIC_URL
```

### 6. Point your provider's number at the webhook

Both providers use the **same** answer webhook:

```
https://YOUR_NGROK_URL/incoming-call   (HTTP POST)
```

- **Twilio:** set it as the phone number's Voice webhook.
- **Vobiz:** set it as the number's / inbound SIP trunk's answer URL.

### 7. Test outbound

```bash
curl http://localhost:8080/make-call
```

Dials `MY_INDIAN_NUMBER` via whichever provider `TELEPHONY` selects.

---

## File structure

```
server.py              FastAPI entry — 3 provider-agnostic routes
bot.py                 Pipecat pipeline — STT → LLM → TTS, idle/end-call, latency taps
services/
  telephony.py         ★ provider adapter — Twilio/Vobiz: dial, answer XML, transport
  stt.py               Soniox STT (Telugu, stt-rt-v5)
  tts.py               Cartesia TTS (Sonic-3.5, Bavani, 8kHz)
  llm.py               LLM factories + system prompt (priya persona)
  welcome.py           Pre-rendered welcome audio (cached, zero first-turn TTS latency)
db.py                  Supabase PostgreSQL — call + message logging
requirements.txt
.env.example
```

To add a **third provider**: add three `case` branches in `services/telephony.py`
(`place_call`, `answer_xml`, `build_transport`) and a value for `TELEPHONY`.
Nothing else changes.

---

## Database: why no ORM?

Two tables, four queries. Raw `asyncpg` is the right fit — no abstractions needed.
If you later add tables or complex joins, reach for
[SQLAlchemy 2.x async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
then, not now.

## Features

- **Switchable telephony** — Twilio ↔ Vobiz via one env var, same routes
- **Barge-in** — user interrupts the agent mid-response (Silero VAD + Pipecat interruptions)
- **Echo gate** — drops the bot's own audio re-transcribed during its speech
- **Idle detection** — hangs up after sustained silence with a Telugu farewell
- **Max call duration** — `MAX_CALL_MINUTES` (default 3), LLM generates the goodbye
- **End-call on goodbye** signals (`bye`, `చాలు`, `అయిపోయింది`, …)
- **Latency timeline** — `DEBUG_TTFB=1` logs per-turn cross-stage timing
- All turns logged to Supabase
