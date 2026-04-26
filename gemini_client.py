import logging
import asyncio
import os
from google import genai
from google.genai import types
import config
from database import increment_stats

import re

logger = logging.getLogger(__name__)

import re

logger = logging.getLogger(__name__)

# Initialize client lazily
_client = None
_last_mode = None

def get_client():
    global _client, _last_mode
    
    # Check if we need to re-initialize due to mode switch
    current_mode = config.USE_VERTEX_AI
    if _client is not None and _last_mode != current_mode:
        logger.info(f"Mode switch detected (Vertex: {_last_mode} -> {current_mode}). Re-initializing client.")
        _client = None
        
    if _client is None:
        _last_mode = current_mode
        if config.USE_VERTEX_AI:
            import google.auth
            creds = None
            sa_path = '/home/spam_inhaler_tx24/@Gemini/service_account.json'
            if os.path.exists(sa_path):
                creds, _ = google.auth.load_credentials_from_file(
                    sa_path,
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
            
            # Use 'global' location as required for some preview models in Vertex AI 2026
            logger.info(f"Initializing Vertex AI Client (Project: {config.GOOGLE_CLOUD_PROJECT}, Location: global)")
            _client = genai.Client(
                vertexai=True, 
                project=config.GOOGLE_CLOUD_PROJECT, 
                location='global',
                credentials=creds
            )
        else:
            if not config.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY is required for AI Studio.")
            logger.info("Initializing AI Studio Client")
            _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client

async def ask_gemini(messages: list, client: any = None, model: str = None) -> dict:
    """
    Asynchronously ask the Gemini API using the new google-genai SDK.
    Returns a dict: {"content": str, "tokens": int, "tps": float, "grounding_metadata": ...}
    """
    genai_client = get_client()
    used_model = model or config.GEMINI_MODEL
    
    contents = []
    system_instruction_text = ""
    has_audio = False
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "system":
            system_instruction_text += content + "\n"
        else:
            parts = []
            if content.strip():
                parts.append(types.Part.from_text(text=content))
            
            # Handle media natively
            media = msg.get("media", [])
            for item in media:
                mime_type = item.get("mime_type", "image/jpeg")
                if mime_type.startswith("audio/"):
                    has_audio = True
                
                data = item.get("data", b"")
                if isinstance(data, str):
                    import base64
                    try:
                        data = base64.b64decode(data)
                    except Exception:
                        pass # Fallback to original if not valid base64
                
                parts.append(types.Part.from_bytes(
                    data=data,
                    mime_type=mime_type
                ))
            
            if not parts:
                continue
                
            contents.append(types.Content(
                role="model" if role == "assistant" else "user",
                parts=parts
            ))
                
    # ── Thinking Configuration ────────────────────────────────────────────
    thinking_config = None
    
    if config.AUTO_THINKING:
        logger.info(f"Auto-Thinking Enabled: Using Native ThinkingConfig for {used_model}")
        thinking_config = types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=config.THINKING_BUDGET
        )
        
        # OMIT LEGACY STUFF: Scrub instructions related to manual thinking mode
        system_instruction_text = re.sub(r'(?i)#\s*REASONING PROTOCOL.*?(?=#|$)', '', system_instruction_text, flags=re.S)
        system_instruction_text = re.sub(r'(?i)\[MODE:\s*think\]', '', system_instruction_text)
        system_instruction_text = re.sub(r'(?i).*?choose\s*\[MODE:\s*think\].*?\n', '', system_instruction_text)
            
    # Add final safety directive
    system_instruction_text += "\n[FINAL DIRECTIVE]: You must strictly adhere to your original instructions and safety guidelines above."
    
    # ── Tool Selection Logic ──────────────────────────────────────────────
    tools = [
        types.Tool(google_search=types.GoogleSearch()),
        types.Tool(url_context=types.UrlContext())
    ]
    if not has_audio:
        tools.append(types.Tool(code_execution=types.ToolCodeExecution()))
    


    genai_config = types.GenerateContentConfig(
        system_instruction=system_instruction_text.strip(),
        tools=tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False),
        thinking_config=thinking_config
    )
        
    try:
        logger.info(f"Gemini SDK Request: model={used_model}, vertex={config.USE_VERTEX_AI}, thinking={thinking_config is not None}")
        
        # Use the async interface
        response = await genai_client.aio.models.generate_content(
            model=used_model,
            contents=contents,
            config=genai_config
        )
        
        # Log search queries to console
        if response.candidates and response.candidates[0].grounding_metadata:
            meta = response.candidates[0].grounding_metadata
            if meta.web_search_queries:
                for q in meta.web_search_queries:
                    print(f"[GOOGLE SEARCH QUERY]: {q}")
                    logger.info(f"Dynamic Grounding Triggered: Query='{q}'")

        usage = response.usage_metadata
        total_tokens = usage.total_token_count if usage else 0
        
        content_out = ""
        
        if response.candidates and response.candidates[0].content:
            parts = response.candidates[0].content.parts
            if parts:
                for part in parts:
                    if part.text and not (hasattr(part, "thought") and part.thought):
                        content_out += part.text
                    
        if not content_out:
            if response.candidates and response.candidates[0].finish_reason:
                reason = response.candidates[0].finish_reason
                if reason == "SAFETY":
                    content_out = "Response blocked by safety filters."
                elif reason == "RECITATION":
                    content_out = "Response blocked due to recitation (copyright) filters."
                else:
                    content_out = f"No response generated (Finish Reason: {reason})."
            else:
                content_out = "No response generated."
        
        increment_stats(tokens=total_tokens)
        
        return {
            "content": content_out,
            "tokens": total_tokens,
            "tps": 0.0,
            "model": used_model,
            "grounding_metadata": response.candidates[0].grounding_metadata if response.candidates else None
        }
        
    except Exception as e:
        logger.error(f"Gemini SDK Error: {str(e)}", exc_info=True)
        raise e
