import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from loguru import logger

from bot import run_bot
from services.telephony import answer_xml, place_call, provider

load_dotenv()

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "running", "provider": provider()}


# Standardized endpoints — same paths for every provider. The active channel is
# chosen by TELEPHONY (twilio|vobiz); all provider logic lives in services/telephony.py.
@app.get("/make-call")
async def make_call(to: str | None = None):
    """Outbound: dials every number in MY_INDIAN_NUMBER (comma-separated) and
    connects each to the bot. Override with ?to=+1...,+91... for a one-off list."""
    answer_url = f"https://{os.getenv('PUBLIC_URL')}/incoming-call"
    raw = to or os.getenv("MY_INDIAN_NUMBER", "")
    number = [n.strip() for n in raw.split(",") if n.strip()]
    
    numbers = ["+918688664337"]
    # ponytail: calls placed sequentially; gather() if you need them fired in parallel
    return {"results": [await place_call(n, answer_url) for n in numbers]}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call():
    """Answer webhook — returns the XML that upgrades the call to /media-stream."""
    body, media_type = answer_xml(f"wss://{os.getenv('PUBLIC_URL')}/media-stream")
    return HTMLResponse(content=body, media_type=media_type)


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """Audio WebSocket. The provider's handshake is parsed inside build_transport."""
    logger.info(f"media-stream WS: connection opening (provider={provider()}, query={dict(websocket.query_params)})")
    await websocket.accept()
    logger.info("media-stream WS: accepted, starting bot")
    try:
        await run_bot(websocket)
        logger.info("media-stream WS: bot finished cleanly")
    except Exception:
        logger.exception("media-stream WS: bot crashed")
        raise


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
