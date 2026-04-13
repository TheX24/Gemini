import httpx
import logging
import config
from gemini_client import ask_gemini
from ollama_client import ask_ollama

logger = logging.getLogger(__name__)

async def ask_llm(messages: list, client: httpx.AsyncClient = None, model: str = None) -> dict:
    """
    Unified entrypoint for LLM inference.
    Routes to Gemini or Ollama based on configuration, with optional fallback.
    """
    if config.USE_GEMINI:
        try:
            return await ask_gemini(messages, client=client, model=model)
        except Exception as e:
            if config.USE_OLLAMA_FALLBACK:
                logger.warning(f"Gemini API failed with error: {e}. Falling back to Ollama.")
                try:
                    return await ask_ollama(messages, client=client) # model is purposely omitted to use Ollama default
                except Exception as ollama_e:
                    logger.error(f"Ollama fallback also failed: {ollama_e}")
                    return {
                        "content": f"Error communicating with LLMs. Gemini: {e} | Ollama: {ollama_e}",
                        "tokens": 0,
                        "tps": 0.0
                    }
            else:
                return {
                    "content": f"Error communicating with Gemini: {e}",
                    "tokens": 0,
                    "tps": 0.0
                }
    else:
        try:
            return await ask_ollama(messages, client=client, model=model)
        except Exception as e:
            return {
                "content": f"Error communicating with local LLM: {str(e)}",
                "tokens": 0,
                "tps": 0.0
            }
