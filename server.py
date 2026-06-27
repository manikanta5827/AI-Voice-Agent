import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

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
async def make_call():
    """Outbound: dials MY_INDIAN_NUMBER and connects it to the bot."""
    answer_url = f"https://{os.getenv('PUBLIC_URL')}/incoming-call"
    return await place_call(os.getenv("MY_INDIAN_NUMBER"), answer_url)


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call():
    """Answer webhook — returns the XML that upgrades the call to /media-stream."""
    body, media_type = answer_xml(f"wss://{os.getenv('PUBLIC_URL')}/media-stream")
    return HTMLResponse(content=body, media_type=media_type)


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """Audio WebSocket. The provider's handshake is parsed inside build_transport."""
    await websocket.accept()
    await run_bot(websocket)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
