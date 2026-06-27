"""Telephony provider adapter — all Twilio/Vobiz-specific wiring in one place.

Switch the active channel with TELEPHONY=twilio|vobiz (default twilio). bot.py
and server.py stay provider-agnostic and call through these three helpers:
  place_call()      outbound dial
  answer_xml()      the answer-webhook body that upgrades the call to the WS
  build_transport() the media-stream transport, incl. each provider's handshake
"""

import json
import os

import aiohttp
from loguru import logger
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse


def provider() -> str:
    """Active channel. Read per-call so a .env change + reload flips it."""
    return os.getenv("TELEPHONY", "twilio").lower()


# --- outbound -------------------------------------------------------------- #
async def place_call(to: str, answer_url: str) -> dict:
    """Outbound dial via the active provider. answer_url is hit when answered."""
    match provider():
        case "vobiz":
            return await _vobiz_dial(to, answer_url)
        case _:
            client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            call = client.calls.create(
                url=answer_url, to=to, from_=os.getenv("TWILIO_US_NUMBER")
            )
            return {"status": "calling", "sid": call.sid, "num": to}


async def _vobiz_dial(to: str, answer_url: str) -> dict:
    auth_id = os.getenv("VOBIZ_AUTH_ID")
    url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/"
    headers = {
        "Content-Type": "application/json",
        "X-Auth-ID": auth_id,
        "X-Auth-Token": os.getenv("VOBIZ_AUTH_TOKEN"),
    }
    data = {
        "to": to,
        "from": os.getenv("VOBIZ_PHONE_NUMBER"),
        "answer_url": answer_url,
        "answer_method": "POST",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 201:
                return {"status": "error", "code": resp.status, "body": body}
    return {"status": "calling", "sid": body.get("request_uuid") or body.get("call_uuid"), "num": to}


# --- answer webhook -------------------------------------------------------- #
def answer_xml(ws_url: str) -> tuple[str, str]:
    """Returns (body, media_type) for the answer webhook: TwiML or Vobiz Stream XML."""
    match provider():
        case "vobiz":
            encoding = os.getenv("VOBIZ_ENCODING", "audio/x-mulaw")
            rate = os.getenv("VOBIZ_SAMPLE_RATE", "8000")
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f'  <Stream bidirectional="true" audioTrack="inbound" '
                f'contentType="{encoding};rate={rate}" keepCallAlive="true">{ws_url}</Stream>\n'
                "</Response>"
            )
            return xml, "application/xml"
        case _:
            response = VoiceResponse()
            connect = Connect()
            connect.stream(url=ws_url)
            response.append(connect)
            return str(response), "text/xml"


# --- media-stream transport ------------------------------------------------ #
async def build_transport(websocket):
    """Builds the media-stream transport for the active provider, consuming that
    provider's start handshake off the socket. Returns (transport, call_id,
    stream_id); both IDs empty means a malformed handshake the caller should drop.
    Both wire formats are MULAW 8kHz — only the serializer/handshake differs."""
    match provider():
        case "vobiz":
            serializer, call_id, stream_id = await _vobiz_serializer(websocket)
        case _:
            serializer, call_id, stream_id = await _twilio_serializer(websocket)

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,  # raw telephony media — no WAV header
            serializer=serializer,
        ),
    )
    return transport, call_id, stream_id


async def _twilio_serializer(websocket):
    # Twilio's stream/call SIDs ride in the 'start' event on the socket.
    stream_sid = call_sid = ""
    async for raw in websocket.iter_text():
        msg = json.loads(raw)
        if msg.get("event") == "start":
            stream_sid = msg["start"]["streamSid"]
            call_sid = msg["start"]["callSid"]
            break
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
    )
    return serializer, call_sid, stream_sid


async def _vobiz_serializer(websocket):
    # Lazy import so Twilio-only installs don't need pipecat-vobiz.
    from pipecat.serializers.vobiz import VobizFrameSerializer, parse_vobiz_start

    # Vobiz's `start` event carries the negotiated format + IDs; reading it also
    # strips the handshake so the transport sees only media.
    parsed = await parse_vobiz_start(websocket)
    call_id = (
        websocket.query_params.get("call_uuid")
        or websocket.query_params.get("call_id")
        or parsed["call_id"]
    )
    stream_id = parsed["stream_id"]
    logger.info(
        f"Vobiz start: call={call_id!r} stream={stream_id!r} "
        f"fmt=({parsed['encoding']!r},{parsed['sample_rate']})"
    )
    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=os.getenv("VOBIZ_AUTH_ID", ""),
        auth_token=os.getenv("VOBIZ_AUTH_TOKEN", ""),
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=parsed["sample_rate"] or int(os.getenv("VOBIZ_SAMPLE_RATE", "8000")),
            encoding=parsed["encoding"] or os.getenv("VOBIZ_ENCODING", "audio/x-mulaw"),
            sample_rate=None,  # inherit pipeline rate, then resample to vobiz_sample_rate
            l16_byte_order=os.getenv("VOBIZ_L16_ENDIAN", "be"),
            auto_hang_up=True,
        ),
    )
    return serializer, call_id, stream_id
