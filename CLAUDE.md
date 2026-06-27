
Implementing Any feature.

Use whatever process is necessary to ensure the change is production-ready.
Review your own work.
Run appropriate validation.
Address any findings.
Continue until you believe the task is complete.
Then present the result.

Use Python 3.11+. Runtime is FastAPI + uvicorn + Pipecat.

- Run server: `python server.py` or `uvicorn server:app --reload --port 8080`
- Install deps: `pip install -r requirements.txt`
- Virtual env at `.venv/` — activate with `source .venv/bin/activate`
- No ORM — raw `asyncpg` for Supabase PostgreSQL
- Pipeline orchestration via Pipecat (STT → LLM → TTS)
- Load `.env` via `python-dotenv` (`load_dotenv()` called in `server.py`)

## Telephony (Twilio / Vobiz)

- The active provider is chosen by the `TELEPHONY` env var (`twilio` | `vobiz`,
  default `twilio`). It is read **per request** in `services/telephony.py:provider()`,
  so a `.env` change + reload switches providers — no code edit.
- ALL provider-specific code lives in `services/telephony.py`. `server.py` and
  `bot.py` are provider-agnostic and must stay that way. Three helpers:
  `place_call()` (outbound), `answer_xml()` (answer webhook body), `build_transport()`
  (media-stream serializer + each provider's start handshake).
- Routes are standardized across providers: `GET /make-call`, `*/incoming-call`,
  `WS /media-stream`. Do not add per-provider routes — branch inside `telephony.py`.
- `bot.run_bot(websocket)` takes only the socket; IDs + transport come from
  `build_transport`. Both providers stream MULAW 8kHz; only serializer/handshake differ.
- Adding a provider = add a `case` to the three helpers + a `TELEPHONY` value. Vobiz
  needs the `pipecat-vobiz` package (lazy-imported so Twilio-only installs don't need it).

