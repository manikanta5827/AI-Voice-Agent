import { SarvamSTT } from "./stt.ts";

async function testSTT() {
  const stt = new SarvamSTT();
  await stt.connect();
  
  // @ts-ignore
  stt.ws!.send(JSON.stringify({
    audio: {
      data: Buffer.from("dummy").toString("base64"),
      encoding: "audio/wav",
      sample_rate: "8000",
    },
  }));
  
  await new Promise(resolve => setTimeout(resolve, 2000));
  stt.disconnect();
}

testSTT().catch(console.error);
