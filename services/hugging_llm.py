import os
import aiohttp
from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings

from business import AGENT_NAME

class HuggingFaceLLMService(LLMService):
    def __init__(self, api_key: str, model_id: str, **kwargs):
        super().__init__(settings=LLMSettings(), **kwargs)
        self._api_key = api_key
        self._model_id = model_id
        self._url = f"https://api-inference.huggingface.co/models/{model_id}"

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        # Guard: only handle context frames, pass everything else straight through
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        try:
            await self.push_frame(LLMFullResponseStartFrame())

            system_prompt = ""
            history = []
            for msg in frame.context.messages:
                match msg["role"]:
                    case "system":
                        system_prompt += msg["content"] + "\n"
                    case "user":
                        history.append(f"User: {msg['content']}")
                    case "assistant":
                        history.append(f"{AGENT_NAME}: {msg['content']}")

            user_msg = "\n".join(history)
            prompt = f"### Instruction:\n{system_prompt.strip()}\n\n### Input:\n{user_msg.strip()}\n\n### Response:\n"

            payload = {
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 512,  # safety net for max tokens
                    "temperature": 0.7,
                    "return_full_text": False,
                },
            }
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self._url, headers=headers, json=payload) as response:
                    if response.status != 200:
                        logger.error(f"HF API Error {response.status}: {await response.text()}")
                        return  # finally still fires the End frame
                    data = await response.json()
                    if not (isinstance(data, list) and data and "generated_text" in data[0]):
                        logger.error(f"Unexpected response format: {data}")
                        return
                    await self.push_frame(LLMTextFrame(data[0]["generated_text"]))
        except Exception as e:
            logger.exception(f"Error calling Hugging Face: {e}")
        finally:
            await self.push_frame(LLMFullResponseEndFrame())

def create_huggingface_llm():
    api_key = os.getenv("HF_TOKEN")
    if not api_key:
        logger.warning("HF_TOKEN environment variable not set. Hugging Face API calls will fail.")
    model_id = "Telugu-LLM-Labs/Indic-gemma-7b-finetuned-sft-Navarasa-2.0"
    return HuggingFaceLLMService(api_key=api_key, model_id=model_id)
