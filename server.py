import json
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from bot import run_bot

load_dotenv()


app = FastAPI()


@app.get("/")
async def root():
    return {"status": "running"}


@app.get("/make-call")
async def make_call():
    client = Client(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
    )
    call = client.calls.create(
        url=f"https://{os.getenv('PUBLIC_URL')}/incoming-call",
        to=os.getenv("MY_INDIAN_NUMBER"),
        from_=os.getenv("TWILIO_US_NUMBER"),
    )
    return {"status": "calling", "sid": call.sid}


@app.post("/incoming-call")
async def incoming_call():
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{os.getenv('PUBLIC_URL')}/media-stream")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="text/xml")


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()

    # Consume Twilio's "connected" + "start" handshake to get stream/call SIDs
    stream_sid = ""
    call_sid = ""
    async for raw in websocket.iter_text():
        msg = json.loads(raw)
        if msg.get("event") == "start":
            stream_sid = msg["start"]["streamSid"]
            call_sid = msg["start"]["callSid"]
            break

    if not stream_sid:
        return

    await run_bot(websocket, stream_sid, call_sid)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
