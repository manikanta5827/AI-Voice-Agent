# Outbound Telugu AI Voice Agent

A complete real-time outbound AI voice agent built using **Bun**, **TypeScript**, **Fastify**, **Twilio (Media Streams)**, **Sarvam AI (STT & TTS)**, and **Google Gemini Flash (LLM)**. 

The agent connects a phone call, greets the user in Telugu, streams real-time audio bidirectionally, transcribes audio to text, generates natural Telugu conversation responses, synthesizes responses back into audio, and streams it back to the caller.

---

## Architecture Flow

```
[ Twilio Outbound Call ] <====== Bidirectional WebSockets (Mulaw 8kHz) ======> [ Fastify Server ]
                                                                                   ||
                                                                                   ||
         +-------------------------------------------------------------------------+
         |
         v
  1. Mulaw -> WAV (16-bit PCM 8kHz)
  2. Sarvam AI Saaras v3 STT REST API ======> [ Transcript (Telugu Script) ]
                                                            ||
                                                            v
                                             3. Google Gemini 2.0 Flash LLM
                                                            ||
                                                            v
  5. Mulaw Audio (8kHz base64) <==== 4. Sarvam AI Bulbul v3 TTS (Telugu Anushka Voice)
```

---

## Features

- **G.711 Mu-law Decoding & Encoding:** Written in pure TypeScript with zero external OS or binary audio dependencies (no `ffmpeg` needed!).
- **Smart Silence & Buffer Controller:** Accurately gathers incoming audio, handles chunk intervals, triggers transcription at silence gaps, and halts user audio capture while the agent response is playing to prevent echo loops.
- **Telugu script response generation:** Fully configured model with Gemini Flash 2.0.

---

## Setup Instructions

### 1. Prerequisites
Ensure you have [Bun](https://bun.sh) installed.

### 2. Installation
Install the project dependencies:
```bash
bun install
```

### 3. Environment Variables
Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

Define the variables:
- `TWILIO_ACCOUNT_SID` & `TWILIO_AUTH_TOKEN`: Find these on your Twilio console dashboard.
- `TWILIO_US_NUMBER`: A purchased Twilio US phone number (must support voice calls).
- `MY_INDIAN_NUMBER`: Your target mobile number (e.g., `+91XXXXXXXXXX`).
- `SARVAM_API_KEY`: API key from [Sarvam AI](https://www.sarvam.ai/).
- `GEMINI_API_KEY`: Google Generative AI key from Google AI Studio.
- `PUBLIC_URL`: The public-facing host url of your server (do **not** include `https://` or `wss://` prefixes).

### 4. Create a Local Tunnel (ngrok / Cloudflare)
Twilio needs to connect to your local server. Start an ngrok tunnel on port 8080:
```bash
ngrok http 8080
```
Copy the forwarding domain (e.g. `1234-56-78.ngrok-free.app`) and paste it as `PUBLIC_URL` in your `.env` file (e.g. `PUBLIC_URL=1234-56-78.ngrok-free.app`).

---

## Running the Server

Start the Fastify server using Bun:
```bash
bun run index.ts
```

---

## Triggering the Outbound Call

Once the server is running and the tunnel is set up, make a GET request to trigger the outbound call:
```bash
curl http://localhost:8080/make-call
```
Alternatively, visit `http://localhost:8080/make-call` in your web browser.

The server will instruct Twilio to dial your mobile number. When you answer the call, the Telugu AI agent will greet you and start responding to whatever you speak.

---

## Codebase Map

- [index.ts](file:///Users/manikanta/Documents/personal-projects/AI%20Voice%20Agent/index.ts): Main Fastify server, Webhook handles, and Media Stream WebSocket loops.
- [llm.ts](file:///Users/manikanta/Documents/personal-projects/AI%20Voice%20Agent/llm.ts): Gemini Flash 2.0 client configuring Telugu speech constraints, persona guidelines, and call-history retention.
- [stt.ts](file:///Users/manikanta/Documents/personal-projects/AI%20Voice%20Agent/stt.ts): Standard G.711 Mu-law decoding to PCM, WAV header preparation, and Sarvam Saaras v3 STT.
- [tts.ts](file:///Users/manikanta/Documents/personal-projects/AI%20Voice%20Agent/tts.ts): Sarvam Bulbul v3 TTS synthesis, PCM parsing, and conversion back to Twilio's Mu-law standard.
