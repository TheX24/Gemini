import logging
from ollama_client import ask_ollama_json

logger = logging.getLogger(__name__)

async def classify_request_llm(message_content: str, reply_context: str | None, client) -> dict:
    """
    Classify the user's message using an LLM router.
    Leverages OLLAMA_ROUTER_MODEL to parse the prompt context and determine the ideal path.
    """
    cleaned_input = message_content.lower()
    
    # Priority 1: Explicit User Overrides
    if "--fast" in cleaned_input:
        return {"route": "fast", "reason": "Explicit user override flag --fast"}
    if "--search" in cleaned_input and "--think" in cleaned_input:
        return {"route": "search_think", "reason": "Explicit user override flags --search and --think"}
    if "--search" in cleaned_input:
        return {"route": "search", "reason": "Explicit user override flag --search"}
    if "--think" in cleaned_input:
        return {"route": "think", "reason": "Explicit user override flag --think"}

    # Formulate context for the LLM
    context_str = f"Replying to context: {reply_context}\n" if reply_context else ""
    user_str = f"User message: {message_content}"
    
    # Send the raw string to the structured json API
    # Since ask_ollama_json already provides the system prompt, we just provide the user input.
    # WAIT! `ask_ollama_json` in `ollama_client.py` has a specific system prompt right now purely for tool extraction.
    # I should pass a custom system prompt to `ask_ollama_json` or redefine it!
    # Ah, let's look at ask_ollama_json... it uses a hardcoded system prompt inside.
    # I need to either modify ask_ollama_json to take a custom sys prompt, or implement it here.
    
    # I will just write a custom function here so the sys prompt is perfectly tailored to routing!

import httpx
import re
import json
from ollama_client import ask_ollama
from config import OLLAMA_ROUTER_MODEL

async def classify_request_llm(message_content: str, reply_context: str | None, client: httpx.AsyncClient) -> dict:
    cleaned_input = message_content.lower()
    
    # Priority 1: Explicit User Overrides
    if "--fast" in cleaned_input:
        return {"route": "fast", "reason": "Explicit user override flag --fast", "tool": "none"}
    if "--search" in cleaned_input and "--think" in cleaned_input:
        return {"route": "search_think", "reason": "Explicit user override flags --search and --think", "tool": "none"}
    if "--search" in cleaned_input:
        return {"route": "search", "reason": "Explicit user override flag --search", "tool": "none"}
    if "--think" in cleaned_input:
        return {"route": "think", "reason": "Explicit user override flag --think", "tool": "none"}

    sys_prompt = {
        "role": "system",
        "content": (
            "You are a Universal Intent Router. Your job is to classify the user's intent with extreme precision. "
            "Output ONLY valid raw JSON with this exact schema:\n"
            "{\n"
            "  \"route\": \"search|think|fast|action\",\n"
            "  \"reason\": \"<string>\",\n"
            "  \"search_query\": \"<string>\", (if search)\n"
            "  \"tool\": \"none|reminder|memory_save|summarize|weather|stats|calculate|translate\",\n"
            "  \"expression\": \"<string>\", (if calculate)\n"
            "  \"text\": \"<string>\", (if translate)\n"
            "  \"target_lang\": \"<string>\", (if translate)\n"
            "  \"location\": \"<string>\", (if weather, e.g. 'London')\n"
            "  \"time_seconds\": <int>, (if reminder)\n"
            "  \"topic\": \"<string>\", (if reminder or memory)\n"
            "  \"message_count\": <int> (if summarize)\n"
            "}\n"
            "Rules for Route Selection:\n"
            "- 'action': Use ONLY if one of the specific tools (calculate, translate, weather, reminder, memory_save, summarize, stats) is clearly requested. "
            "If the request is ambiguous but fits a tool, choose it. If not, use 'fast'.\n"
            "- 'search': Use for current events, news, or broad facts not in your local training data.\n"
            "- 'think': Use for coding, complex multi-step reasoning, philosophical queries, or critiques.\n"
            "- 'fast': Default for casual chat, greetings, or answering based on history. "
            "When in doubt, use 'fast'.\n"
            "Example for calculate: {\"route\": \"action\", \"tool\": \"calculate\", \"expression\": \"(15.5 * 2) + 4\"}\n"
            "Example for translate: {\"route\": \"action\", \"tool\": \"translate\", \"text\": \"hello\", \"target_lang\": \"Spanish\"}\n"
        )
    }
    
    context_str = f"Replying to: {reply_context}\n" if reply_context else ""
    user_msg = {"role": "user", "content": f"{context_str}User: {message_content}"}
    
    try:
        result = await ask_ollama([sys_prompt, user_msg], think=False, client=client, model=OLLAMA_ROUTER_MODEL)
        
        match = re.search(r'(\{.*\})', result, re.DOTALL)
        json_str = match.group(1) if match else result
        
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Fallback for common LLM JSON syntax errors (like trailing commas or missing/single quotes)
            import ast
            try:
                # ast.literal_eval is safer than eval for parsing python-like dict syntax
                data = ast.literal_eval(json_str)
            except:
                data = {"route": "fast", "tool": "none"}
                
        if "route" not in data:
            data["route"] = "fast"
        return data
    except Exception as e:
        logger.error(f"Failed to parse LLM route: {e}")
        return {"route": "fast", "reason": "Fallback due to unexpected error", "tool": "none"}
