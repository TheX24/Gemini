import httpx
import logging
import re
import config
from gemini_client import ask_gemini
from ollama_client import ask_ollama

logger = logging.getLogger(__name__)

def extract_error_code(e: Exception) -> int:
    """Extracts a 3-digit HTTP status code from an exception object or string."""
    # Check for direct status_code attribute (common in many SDKs)
    if hasattr(e, 'status_code'):
        return int(e.status_code)
    
    # Check for google-genai or api_core attributes
    if hasattr(e, 'code'):
        if isinstance(e.code, int):
            return e.code
            
    # Fallback to regex search in the error message
    match = re.search(r'\b(\d{3})\b', str(e))
    return int(match.group(1)) if match else 500

async def ask_llm(messages: list, client: httpx.AsyncClient = None, model: str = None) -> dict:
    """
    Unified entrypoint for LLM inference.
    Routes to Gemini or Ollama based on configuration, with optional fallback.
    """
    if config.USE_GEMINI:
        try:
            return await ask_gemini(messages, client=client, model=model)
        except Exception as e:
            gemini_err_code = extract_error_code(e)
            if config.USE_OLLAMA_FALLBACK:
                logger.warning(f"Gemini API failed with error: {e}. Falling back to Ollama.")
                try:
                    return await ask_ollama(messages, client=client)
                except Exception as ollama_e:
                    logger.error(f"Ollama fallback also failed: {ollama_e}")
                    # Prioritize the Gemini error code as it was the primary request
                    return {
                        "content": f"🚨 [LLM_ERROR]: Gemini: {e} | Ollama: {ollama_e}",
                        "error_code": gemini_err_code,
                        "tokens": 0,
                        "tps": 0.0
                    }
            else:
                return {
                    "content": f"🚨 [LLM_ERROR]: Gemini: {e}",
                    "error_code": gemini_err_code,
                    "tokens": 0,
                    "tps": 0.0
                }
    else:
        try:
            return await ask_ollama(messages, client=client, model=model)
        except Exception as e:
            return {
                "content": f"🚨 [LLM_ERROR]: Local LLM: {str(e)}",
                "error_code": extract_error_code(e),
                "tokens": 0,
                "tps": 0.0
            }
