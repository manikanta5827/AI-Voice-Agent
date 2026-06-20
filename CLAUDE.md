Use Python 3.11+. Runtime is FastAPI + uvicorn + Pipecat.

- Run server: `python server.py` or `uvicorn server:app --reload --port 8080`
- Install deps: `pip install -r requirements.txt`
- Virtual env at `.venv/` — activate with `source .venv/bin/activate`
- No ORM — raw `asyncpg` for Supabase PostgreSQL
- Pipeline orchestration via Pipecat (STT → LLM → TTS)
- Load `.env` via `python-dotenv` (`load_dotenv()` called in `server.py`)
