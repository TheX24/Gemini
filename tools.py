import logging
import httpx

# Set up logging for tools
logger = logging.getLogger(__name__)

async def weather(city: str, client: httpx.AsyncClient = None) -> dict:
    """
    Fetch current weather for a location via wttr.in.
    """
    logger.info(f"Weather requested for city: '{city}'")
    
    # Setup temporary client if none provided
    own_client = False
    if client is None:
        client = httpx.AsyncClient()
        own_client = True
        
    try:
        # Use wttr.in with JSON format
        url = f"https://wttr.in/{city}?format=j1"
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()
        
        data = response.json()
        current = data.get("current_condition", [{}])[0]
        
        return {
            "city": city,
            "temp_C": current.get("temp_C"),
            "temp_F": current.get("temp_F"),
            "condition": current.get("weatherDesc", [{}])[0].get("value"),
            "humidity": current.get("humidity"),
            "wind_speed": current.get("windspeedKmph"),
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Weather API Error: {e}")
        return {
            "city": city,
            "error": str(e),
            "status": "error",
            "message": "Note: Weather information is temporarily unavailable."
        }
    finally:
        if own_client:
            await client.aclose()

async def parse_duration(duration_str: str) -> int | None:
    """
    Parse a duration string like '10m', '1h', '30s', '1.5h', '10 minutes' into seconds.
    Returns None if parsing fails.
    """
    import re
    
    # Clean string
    duration_str = duration_str.lower().strip().replace(" ", "")
    
    # Try regex match for common patterns
    # Matches: 10, 10s, 10m, 10h, 1.5h, etc.
    match = re.match(r'^([\d\.]+)([smhdw])?$', duration_str)
    if not match:
        # Try words
        match = re.match(r'^([\d\.]+)(sec|second|min|minute|hr|hour|day|week)s?$', duration_str)
        
    if match:
        val = float(match.group(1))
        unit = match.group(2)
        
        if unit in (None, 's', 'sec', 'second'):
            return int(val)
        if unit in ('m', 'min', 'minute'):
            return int(val * 60)
        if unit in ('h', 'hr', 'hour'):
            return int(val * 3600)
        if unit in ('d', 'day'):
            return int(val * 86400)
        if unit in ('w', 'week'):
            return int(val * 604800)
            
    return None

async def calculate_math(expression: str) -> dict:
    """
    Safely evaluate a mathematical expression.
    """
    import re
    logger.info(f"Math requested: '{expression}'")
    
    # Clean expression and handle common shorthand
    expression = expression.strip().replace("x", "*").replace("÷", "/")
    
    # Safety: Only allow digits, decimals, basic operators, parentheses, and whitespace
    if not re.match(r'^[0-9\.\+\-\*\/\(\)\s\^]+$', expression):
        return {"status": "error", "message": "Invalid characters in expression. Only numbers and basic operators are allowed."}
    
    try:
        # Python's ** is handled via ^ for user convenience if needed, but let's stick to standard python
        # replace ^ with **
        expression = expression.replace("^", "**")
        
        # Use a restricted namespace for eval
        # Note: While still eval, the regex above prevents complex object access.
        result = eval(expression, {"__builtins__": None}, {})
        return {"status": "success", "result": str(result)}
    except Exception as e:
        logger.error(f"Math calculation error: {e}")
        return {"status": "error", "message": str(e)}
