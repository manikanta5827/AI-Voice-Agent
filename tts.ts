const BIAS = 0x84;
const CLIP = 32635;

/**
 * Encodes a single 16-bit PCM sample to an 8-bit G.711 Mu-law byte.
 */
function encodePCMToMuLawByte(pcmSample: number): number {
  let sign = (pcmSample >> 8) & 0x80;
  if (pcmSample < 0) {
    pcmSample = -pcmSample;
    sign = 0x80;
  } else {
    sign = 0x00;
  }

  if (pcmSample > CLIP) {
    pcmSample = CLIP;
  }

  pcmSample += BIAS;

  let exponent = 7;
  for (let mask = 0x4000; (pcmSample & mask) === 0; exponent--) {
    mask >>= 1;
  }

  const mantissa = (pcmSample >> (exponent + 3)) & 0x0f;
  const muByte = ~(sign | (exponent << 4) | mantissa);

  return muByte & 0xff;
}

/**
 * Encodes a buffer of 16-bit PCM little-endian samples to G.711 Mu-law.
 */
function encodePCMToMuLaw(pcmBuffer: Buffer): Buffer {
  const sampleCount = Math.floor(pcmBuffer.length / 2);
  const muLawBuffer = Buffer.alloc(sampleCount);
  for (let i = 0; i < sampleCount; i++) {
    const pcmSample = pcmBuffer.readInt16LE(i * 2);
    muLawBuffer[i] = encodePCMToMuLawByte(pcmSample);
  }
  return muLawBuffer;
}

/**
 * Sends text to Sarvam TTS API and returns a base64 encoded Mu-law 8kHz audio payload.
 * @param text The Telugu text to synthesize
 * @returns Base64 encoded Mu-law 8kHz audio payload (no header) ready for Twilio
 */
export async function textToSpeech(text: string): Promise<string> {
  console.log(`🔊 TTS: Converting text to speech: "${text}"`);

  const apiKey = Bun.env.SARVAM_API_KEY;
  if (!apiKey) {
    console.error("❌ TTS Error: SARVAM_API_KEY is not defined in the environment.");
    throw new Error("SARVAM_API_KEY is missing");
  }

  try {
    const response = await fetch("https://api.sarvam.ai/text-to-speech", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "api-subscription-key": apiKey,
      },
      body: JSON.stringify({
        text: text,
        target_language_code: "te-IN",
        speaker: "ratan",
        model: "bulbul:v3",
        speech_sample_rate: 8000,
        enable_preprocessing: true,
        output_audio_codec: "wav", // We get WAV first, then extract raw PCM and encode to Mulaw
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Sarvam TTS returned status ${response.status}: ${errorText}`);
    }

    const data = (await response.json()) as { audios?: string[] };
    if (!data.audios || data.audios.length === 0 || !data.audios[0]) {
      throw new Error("Sarvam TTS returned empty audio array");
    }

    // The audio is base64 encoded WAV format
    const base64Wav = data.audios[0];
    let wavBuffer = Buffer.from(base64Wav, "base64");

    // Extract raw PCM (16-bit LE, 8kHz, mono) from WAV buffer.
    // A standard WAV header is 44 bytes, check for 'RIFF' header
    let pcmBuffer: Buffer;
    if (wavBuffer.toString("ascii", 0, 4) === "RIFF") {
      // Find the 'data' subchunk to skip it dynamically, or fallback to skipping the 44-byte header
      let dataOffset = 44;
      for (let i = 12; i < wavBuffer.length - 8; i++) {
        if (wavBuffer.toString("ascii", i, i + 4) === "data") {
          dataOffset = i + 8;
          break;
        }
      }
      pcmBuffer = wavBuffer.subarray(dataOffset);
    } else {
      pcmBuffer = wavBuffer;
    }

    // Convert PCM (16-bit) to Mu-law (8-bit)
    const mulawBuffer = encodePCMToMuLaw(pcmBuffer);
    const base64Mulaw = mulawBuffer.toString("base64");

    console.log(`🔊 TTS: Successfully synthesized and converted to Mu-law. Payload length: ${base64Mulaw.length} base64 chars.`);
    return base64Mulaw;
  } catch (error) {
    console.error("❌ TTS Error: Failed to synthesize speech", error);
    throw error;
  }
}
