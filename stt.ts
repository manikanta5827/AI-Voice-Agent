/**
 * Soniox real-time STT over WebSocket.
 * Lazy-connects on first audio chunk. Sends raw MULAW bytes directly (no conversion).
 * Uses endpoint_detection to fire onFinalTranscript when speaker pauses.
 */
export class SonioxSTT {
  private ws: WebSocket | null = null;
  private connectPromise: Promise<void> | null = null;
  private finalText = "";
  private flushTimer: Timer | null = null;

  onFinalTranscript: ((transcript: string) => void) | null = null;
  onError: ((err: Error) => void) | null = null;

  private ensureConnected(): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN) return Promise.resolve();
    if (this.connectPromise) return this.connectPromise;

    this.connectPromise = new Promise<void>((resolve, reject) => {
      this.ws = new WebSocket("wss://stt-rt.soniox.com/transcribe-websocket");

      this.ws.onopen = () => {
        this.ws!.send(JSON.stringify({
          api_key:                  Bun.env.SONIOX_API_KEY,
          model:                    "stt-rt-v5",
          language_hints:           ["te"],
          enable_endpoint_detection: true,
          audio_format:             "mulaw",
          sample_rate:              8000,
          num_channels:             1,
        }));
        console.log("🎙️ STT: Soniox connected");
        resolve();
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data as string) as {
            tokens?: Array<{ text: string; is_final: boolean }>;
            error_code?: string;
            error_message?: string;
          };

          if (msg.error_code) {
            console.error("❌ STT: Soniox error —", msg.error_message);
            this.onError?.(new Error(msg.error_message ?? msg.error_code));
            return;
          }

          const tokens = msg.tokens ?? [];
          let gotFinal = false;
          let hasNonFinal = false;

          for (const token of tokens) {
            if (token.is_final) {
              this.finalText += token.text;
              gotFinal = true;
            } else {
              hasNonFinal = true;
            }
          }

          if (hasNonFinal) {
            // User still speaking — cancel any pending flush
            if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null; }
          } else if (gotFinal && this.finalText.trim()) {
            // Endpoint detected: all tokens final, none pending → speaker paused
            if (this.flushTimer) clearTimeout(this.flushTimer);
            this.flushTimer = setTimeout(() => {
              this.flushTimer = null;
              const transcript = this.finalText.trim().replace(/<end>/gi, "").trim();
              this.finalText = "";
              if (transcript) {
                console.log(`🎙️ STT: "${transcript}"`);
                this.onFinalTranscript?.(transcript);
              }
            }, 150); // 150ms grace — catches rapid final-token bursts from endpoint detection
          }
        } catch (e) {
          console.error("❌ STT: Message parse error", e);
        }
      };

      this.ws.onerror = () => {
        const err = new Error("STT WebSocket error");
        this.onError?.(err);
        reject(err);
      };

      this.ws.onclose = (event: CloseEvent) => {
        console.log(`🎙️ STT: WebSocket closed — code: ${event.code}`);
        this.ws = null;
      };
    }).finally(() => { this.connectPromise = null; });

    return this.connectPromise;
  }

  // Fire-and-forget — index.ts calls this without await
  sendChunk(mulawBuffer: Buffer): void {
    this.ensureConnected()
      .then(() => { if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(mulawBuffer); })
      .catch(e => console.error("❌ STT: sendChunk error", e));
  }

  disconnect(): void {
    if (this.flushTimer) { clearTimeout(this.flushTimer); this.flushTimer = null; }
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(""); // signal end-of-audio
    this.ws?.close();
    this.ws = null;
    this.finalText = "";
  }
}
