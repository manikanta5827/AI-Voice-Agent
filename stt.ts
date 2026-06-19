// Initialize G.711 Mu-law decode table
const MU_LAW_DECODE_TABLE = new Int16Array(256);
for (let i = 0; i < 256; i++) {
  const mu = ~i;
  const sign = mu & 0x80;
  const exponent = (mu & 0x70) >> 4;
  const mantissa = mu & 0x0f;
  let sample = ((mantissa << 3) + 132) << exponent;
  sample -= 132;
  MU_LAW_DECODE_TABLE[i] = sign ? -sample : sample;
}

/**
 * Decodes a Mu-law buffer (8kHz) to a 16-bit PCM little-endian buffer.
 */
function decodeMuLawToPCM(muLawBuffer: Buffer): Buffer {
  const pcmBuffer = Buffer.alloc(muLawBuffer.length * 2);
  for (let i = 0; i < muLawBuffer.length; i++) {
    const muByte = muLawBuffer[i]!;
    const pcmSample = MU_LAW_DECODE_TABLE[muByte]!;
    pcmBuffer.writeInt16LE(pcmSample, i * 2);
  }
  return pcmBuffer;
}

/**
 * Prepends a 44-byte WAV header to 16-bit PCM mono audio.
 */
function pcmToWav(pcmBuffer: Buffer, sampleRate: number = 8000): Buffer {
  const header = Buffer.alloc(44);
  const dataLength = pcmBuffer.length;

  // "RIFF" chunk descriptor
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + dataLength, 4);
  header.write("WAVE", 8);

  // "fmt " sub-chunk
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16); // Subchunk1Size (16 for PCM)
  header.writeUInt16LE(1, 20);  // AudioFormat (1 = PCM)
  header.writeUInt16LE(1, 22);  // NumChannels (1 = Mono)
  header.writeUInt32LE(sampleRate, 24); // SampleRate (8000)
  header.writeUInt32LE(sampleRate * 2, 28); // ByteRate (SampleRate * NumChannels * BitsPerSample/8 = 16000)
  header.writeUInt16LE(2, 32);  // BlockAlign (NumChannels * BitsPerSample/8 = 2)
  header.writeUInt16LE(16, 34); // BitsPerSample (16)

  // "data" sub-chunk
  header.write("data", 36);
  header.writeUInt32LE(dataLength, 40);

  return Buffer.concat([header, pcmBuffer]);
}

/**
 * Sends a raw Mu-law audio buffer to Sarvam STT after converting it to WAV.
 * @param mulawBuffer Raw 8kHz Mu-law audio buffer from Twilio
 * @returns The transcribed text
 */
export async function transcribeAudio(mulawBuffer: Buffer): Promise<string> {
  console.log(`🎙️ STT: Received ${mulawBuffer.length} bytes of Mu-law audio. Transcribing...`);

  const apiKey = Bun.env.SARVAM_API_KEY;
  if (!apiKey) {
    console.error("❌ STT Error: SARVAM_API_KEY is not defined in the environment.");
    throw new Error("SARVAM_API_KEY is missing");
  }

  try {
    // 1. Convert Twilio 8kHz Mu-law to 16-bit linear PCM
    const pcmBuffer = decodeMuLawToPCM(mulawBuffer);

    // 2. Prepend WAV header (required by Sarvam API)
    const wavBuffer = pcmToWav(pcmBuffer, 8000);

    // 3. Prepare FormData
    const formData = new FormData();
    const wavBlob = new Blob([wavBuffer], { type: "audio/wav" });
    
    formData.append("file", wavBlob, "audio.wav");
    formData.append("model", "saaras:v3");
    formData.append("language_code", "te-IN");
    formData.append("mode", "transcribe");

    // 4. Send request to Sarvam STT REST API
    const response = await fetch("https://api.sarvam.ai/speech-to-text", {
      method: "POST",
      headers: {
        "api-subscription-key": apiKey,
      },
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Sarvam STT returned status ${response.status}: ${errorText}`);
    }

    const data = (await response.json()) as { transcript?: string };
    const transcript = data.transcript?.trim() || "";

    console.log(`🎙️ STT: Transcription result: "${transcript}"`);
    return transcript;
  } catch (error) {
    console.error("❌ STT Error: Failed to transcribe audio", error);
    throw error;
  }
}
