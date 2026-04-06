import logging
import httpx
from config import SEARXNG_BASE_URL, SEARXNG_FORMAT, SEARXNG_CATEGORIES

# Set up logging for tools
logger = logging.getLogger(__name__)

async def search_web(query: str, client: httpx.AsyncClient = None) -> dict:
    """
    Search the web using SearXNG via HTTP interface.
    """
    logger.info(f"SearXNG requested for query: '{query}'")
    
    # Setup temporary client if none provided
    own_client = False
    if client is None:
        client = httpx.AsyncClient()
        own_client = True
        
    try:
        url = f"{SEARXNG_BASE_URL}/search"
        params = {
            "q": query,
            "format": SEARXNG_FORMAT,
            "categories": SEARXNG_CATEGORIES
        }
        
        response = await client.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        
        data = response.json()
        
        results = data.get("results", [])
        
        # Only extract useful fields
        extracted_results = []
        for res in results[:5]:  # Limit to top 5 results
            extracted = {
                "title": res.get("title", ""),
                "snippet": res.get("content", ""),
                "url": res.get("url", "")
            }
            if res.get("engine"):
                extracted["source"] = res.get("engine")
            extracted_results.append(extracted)
            
        return {
            "query": query,
            "results": extracted_results,
            "answer": data.get("answers", []),
            "suggestions": data.get("suggestions", []),
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"SearXNG API Error: {e}")
        return {
            "query": query,
            "results": [],
            "error": str(e),
            "status": "error",
            "message": "Note: Web search is temporarily unavailable."
        }
    finally:
        if own_client:
            await client.aclose()

async def calculate_math(expression: str) -> dict:
    """
    Perform a mathematical calculation safely.
    """
    import math
    logger.info(f"Calculator requested for expression: '{expression}'")
    
    # Safe list of math functions and constants
    safe_dict = {
        "abs": abs, "pow": pow, "round": round, "min": min, "max": max,
        "sum": sum, "range": range, "len": len,
        "math": math, "acos": math.acos, "asin": math.asin, "atan": math.atan,
        "atan2": math.atan2, "ceil": math.ceil, "cos": math.cos,
        "cosh": math.cosh, "degrees": math.degrees, "exp": math.exp,
        "fabs": math.fabs, "floor": math.floor, "hypot": math.hypot,
        "log": math.log, "log10": math.log10, "pi": math.pi,
        "radians": math.radians, "sin": math.sin, "sinh": math.sinh,
        "sqrt": math.sqrt, "tan": math.tan, "tanh": math.tanh, "e": math.e
    }
    
    try:
        # Pre-process expression to handle common cases
        expr = expression.replace("^", "**") # LLMs often use ^ for exponent
        
        # Evaluate with NO builtins for safety
        result = eval(expr, {"__builtins__": None}, safe_dict)
        
        return {
            "expression": expression,
            "result": result,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Calculator Error: {e}")
        return {
            "expression": expression,
            "error": str(e),
            "status": "error",
            "message": f"Could not solve expression: {e}"
        }

async def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> dict:
    """
    Translation tool using a structured frame for the LLM to process.
    """
    logger.info(f"Translation requested: '{text}' -> {target_lang}")
    
    # This tool primarily relies on the core LLM's multi-lingual knowledge,
    # but we provide a structured object to help the LLM form the response.
    return {
        "text": text,
        "target_lang": target_lang,
        "source_lang": source_lang,
        "status": "success",
        "instruction": f"Please translate the following text from {source_lang} to {target_lang}. Return ONLY the translated text."
    }
