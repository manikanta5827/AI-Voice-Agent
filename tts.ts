/**
 * Persistent WebSocket connection to Cartesia Sonic TTS.
 * Accepts text sentences, fires onAudioChunk with MULAW bytes for Twilio.
 * Fires onComplete after synthesis of all queued text finishes.
 * Each pipeline turn gets a fresh context_id so Cartesia tracks turn boundaries.
 */
export class CartesiaTTS {
  private ws: WebSocket | null = null;
  private readonly apiKey: string;
  private readonly voiceId: string;
  private contextId = "";
  private _totalMulawBytes = 0;

  onAudioChunk: ((mulawBuffer: Buffer) => void) | null = null;
  onComplete: (() => void) | null = null;

  constructor() {
    this.apiKey = Bun.env.CARTESIA_API_KEY || "";
    this.voiceId = Bun.env.CARTESIA_VOICE_ID || "";
    if (!this.apiKey) console.warn("⚠️ CARTESIA_API_KEY not set");
    if (!this.voiceId) console.warn("⚠️ CARTESIA_VOICE_ID not set");
  }

  get totalMulawBytes(): number { return this._totalMulawBytes; }

  resetByteCount(): void {
    this._totalMulawBytes = 0;
    this.contextId = crypto.randomUUID(); // new context per pipeline turn
  }

  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket("wss://api.cartesia.ai/tts/websocket", {
        headers: {
          "X-API-Key": this.apiKey,
          "Cartesia-Version": "2024-06-10",
        },
      } as any);

      this.ws.onopen = () => {
        console.log("🔊 TTS: Cartesia WebSocket connected");
        resolve();
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data as string) as {
            type: string;
            data?: string;
            error?: string;
          };

          if (msg.type === "chunk" && msg.data) {
            const mulaw = Buffer.from(msg.data, "base64");
            this._totalMulawBytes += mulaw.length;
            this.onAudioChunk?.(mulaw);
          } else if (msg.type === "done") {
            console.log(`🔊 TTS: Synthesis complete — ${this._totalMulawBytes} MULAW bytes`);
            this.onComplete?.();
          } else if (msg.type === "error") {
            console.error("❌ TTS: Cartesia error —", msg.error);
          }
        } catch (e) {
          console.error("❌ TTS: Message parse error", e);
        }
      };

      this.ws.onerror = (e: Event) => {
        console.error("❌ TTS: WebSocket error", e);
        reject(new Error(`TTS WebSocket error: ${e}`));
      };

      this.ws.onclose = (event: CloseEvent) => {
        console.log(`🔊 TTS: WebSocket closed — code: ${event.code}, reason: "${event.reason}"`);
        this.ws = null;
      };
    });
  }

  private base() {
    return {
      model_id: "sonic-3.5",
      voice: { mode: "id", id: this.voiceId },
      output_format: { container: "raw", encoding: "pcm_mulaw", sample_rate: 8000 },
      language: "te",
      context_id: this.contextId,
    };
  }

  sendText(text: string): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ ...this.base(), transcript: text, continue: true }));
  }

  // Signal end of turn — Cartesia flushes remaining audio and fires "done"
  flush(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ ...this.base(), transcript: "", continue: false }));
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
  }
}
