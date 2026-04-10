import httpx
import logging
import base64
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_NUM_CTX
from database import increment_stats

logger = logging.getLogger(__name__)

async def ask_ollama(messages: list, think: bool = False, client: httpx.AsyncClient = None, model: str = None) -> dict:
    """
    Asynchronously ask the local Ollama instance for a response.
    Returns a dict: {"content": str, "tokens": int, "tps": float}
    """
    used_model = model or OLLAMA_MODEL
    
    # Use standard httpx client if none provided
    if client is None:
        async with httpx.AsyncClient() as new_client:
            return await _call_ollama(messages, think, new_client, used_model)
    else:
        return await _call_ollama(messages, think, client, used_model)

async def _call_ollama(messages: list, think: bool, client: httpx.AsyncClient, model: str) -> dict:
    url = f"{OLLAMA_BASE_URL}/api/chat"
    
    # Avoid side-effects on the original messages list
    final_messages = list(messages)

    # SYSTEM SANDWICH: For non-thinking routes, repeat a brief safety directive.
    # If the last message contains images, we insert the directive BEFORE it 
    # to ensure the model's direct multimodal perception remains the active focus.
    if not think:
        directive = {
            "role": "system", 
            "content": "[FINAL DIRECTIVE]: You must strictly adhere to your original instructions and safety guidelines above."
        }
        
        has_images = final_messages and "images" in final_messages[-1]
        if has_images:
            final_messages.insert(-1, directive)
        else:
            final_messages.append(directive)

    payload = {
        "model": model,
        "messages": final_messages,
        "stream": False,
        "think": think,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX
        }
    }
    
    try:
        logger.info(f"Ollama Request: route={'think' if think else 'fast'}, model={model}, ctx={OLLAMA_NUM_CTX}")
        
        response = await client.post(url, json=payload, timeout=300.0)
        response.raise_for_status()
        
        data = response.json()
        
        # Track token usage
        prompt_tokens = data.get("prompt_eval_count", 0)
        eval_tokens = data.get("eval_count", 0)
        eval_duration_ns = data.get("eval_duration", 0)
        
        tps = 0.0
        if eval_duration_ns > 0:
            tps = eval_tokens / (eval_duration_ns / 1e9)
        
        # Update database stats
        increment_stats(tokens=prompt_tokens + eval_tokens)
        
        return {
            "content": data.get("message", {}).get("content", ""),
            "tokens": prompt_tokens + eval_tokens,
            "tps": tps
        }
        
    except Exception as e:
        logger.error(f"Ollama Request Failed: {str(e)}")
        return {
            "content": f"Error communicating with local LLM: {str(e)}",
            "tokens": 0,
            "tps": 0.0
        }

# --- Legacy ask_ollama_vision was removed as multimodal support is now native in ask_ollama ---
