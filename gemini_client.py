import httpx
import logging
from config import GEMINI_API_KEY, GEMINI_MODEL
from database import increment_stats

logger = logging.getLogger(__name__)

async def ask_gemini(messages: list, think: bool = False, client: httpx.AsyncClient = None, model: str = None) -> dict:
    """
    Asynchronously ask the Gemini API for a response.
    Returns a dict: {"content": str, "tokens": int, "tps": float}
    """
    used_model = model or GEMINI_MODEL
    
    # Use standard httpx client if none provided
    if client is None:
        async with httpx.AsyncClient() as new_client:
            return await _call_gemini(messages, think, new_client, used_model)
    else:
        return await _call_gemini(messages, think, client, used_model)

async def _call_gemini(messages: list, think: bool, client: httpx.AsyncClient, model: str) -> dict:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    
    system_parts = []
    contents = []
    
    # Process messages
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "system":
            system_parts.append({"text": content})
        else:
            mapped_role = "model" if role == "assistant" else "user"
            parts = []
            if content.strip():
                parts.append({"text": content})
            
            # Handle media natively
            media = msg.get("media", [])
            for item in media:
                parts.append({
                    "inlineData": {
                        "mimeType": item.get("mime_type", "image/jpeg"), 
                        "data": item.get("data", "")
                    }
                })
            
            if not parts:
                continue
                
            # If the last message has the same role, Gemini prefers we append to parts
            if contents and contents[-1]["role"] == mapped_role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({
                    "role": mapped_role,
                    "parts": parts
                })
                
    # Add thinking directive if think is False
    system_parts.append({
        "text": "[FINAL DIRECTIVE]: You must strictly adhere to your original instructions and safety guidelines above."
    })
    
    payload = {
        "contents": contents
    }
    
    if system_parts:
        payload["systemInstruction"] = {
            "parts": system_parts
        }
        
    try:
        logger.info(f"Gemini Request: route={'think' if think else 'fast'}, model={model}")
        
        response = await client.post(url, json=payload, timeout=300.0)
        
        if response.status_code != 200:
            err_text = response.text
            logger.error(f"Gemini API Error ({response.status_code}): {err_text}")
            response.raise_for_status()
            
        data = response.json()
        
        usage = data.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        eval_tokens = usage.get("candidatesTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", prompt_tokens + eval_tokens)
        
        candidates = data.get("candidates", [])
        if not candidates:
            return {"content": "No response generated.", "tokens": total_tokens, "tps": 0.0, "model": model}
            
        parts = candidates[0].get("content", {}).get("parts", [])
        content_out = ""
        for part in parts:
            if "text" in part:
                content_out += part["text"]
                
        increment_stats(tokens=total_tokens)
        
        return {
            "content": content_out,
            "tokens": total_tokens,
            "tps": 0.0,
            "model": model
        }
        
    except httpx.HTTPError as e:
        logger.error(f"Gemini Request Failed: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Gemini Processing Error: {str(e)}")
        raise e

