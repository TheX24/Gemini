import re
from config import DEFAULT_SYSTEM_PROMPT, MAX_REPLY_CONTEXT_LENGTH

def clean_mention(content: str, user_id: int) -> str:
    """
    Remove the self-bot's mention from the message and normalize whitespace.
    """
    # Regex for user mention in Discord (e.g., <@123456789> or <@!123456789>)
    mention_pattern = rf"<@!?{user_id}>"
    cleaned = re.sub(mention_pattern, "", content)
    
    # Trim and normalize extra whitespace
    return " ".join(cleaned.split()).strip()

def build_context(user_prompt: str, reply_context: str | None = None, is_reply_to_self: bool = True) -> list:
    """
    Construct the final list of messages for Ollama.
    """
    import datetime
    now_str = datetime.datetime.now().strftime("%A, %B %d, %Y, %H:%M:%S")
    
    messages = [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "system", "content": f"[Time Context]: The current date and time is {now_str}."}
    ]
    
    if reply_context:
        # Truncate reply context if too long
        if len(reply_context) > MAX_REPLY_CONTEXT_LENGTH:
            reply_context = reply_context[:MAX_REPLY_CONTEXT_LENGTH] + "..."
            
        role = "assistant" if is_reply_to_self else "system"
        messages.append({
            "role": role,
            "content": f"[Previous context from replied-to message]: {reply_context}"
        })
        
    messages.append({
        "role": "user",
        "content": f"### [USER CONTENT]:\n{user_prompt}\n### [USER CONTENT END]"
    })
    
    return messages
