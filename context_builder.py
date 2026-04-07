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

def build_context(user_prompt: str, reply_context: str | None = None, is_reply_to_self: bool = True, history: list | None = None, recap: str | None = None, user_info: dict | None = None, other_users_info: list | None = None) -> list:
    """
    Construct the final list of messages for Ollama with history and optional recap.
    """
    import datetime
    now_str = datetime.datetime.now().strftime("%A, %B %d, %Y, %H:%M:%S")
    
    messages = [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "system", "content": f"[Time Context]: The current date and time is {now_str}."}
    ]

    def _format_profile(info: dict, label_str: str):
        profile_parts = [
            f"Display Name: {info.get('display_name')}",
            f"Username: {info.get('username')}",
            f"ID: {info.get('id')}",
            f"Account Created: {info.get('created_at')}",
            f"Server: {info.get('server_name')}",
        ]
        
        bio = info.get('bio')
        if bio:
            profile_parts.append(f"About Me (Bio): {bio}")

        pronouns = info.get('pronouns')
        if pronouns:
            profile_parts.append(f"Pronouns: {pronouns}")

        nitro = info.get('premium_since')
        if nitro:
            profile_parts.append(f"Nitro/Boost Status (Tag): Active since {nitro}")
            
        roles = info.get('server_roles')
        if roles:
            profile_parts.append(f"Server Roles: {', '.join(roles)}")
            
        status = info.get('status')
        if status:
            profile_parts.append(f"Active Status: {', '.join(status)}")

        connections = info.get('connections')
        if connections:
            profile_parts.append(f"Connected Accounts: {', '.join(connections)}")

        recent = info.get('recent_activity')
        if recent and recent != "None":
            profile_parts.append(f"Recent Activity: {recent}")

        game_board = info.get('game_board')
        if game_board and game_board != "None":
            profile_parts.append(f"Game Board / Leaderboards: {game_board}")
            
        return f"{label_str}:\n" + "\n".join(profile_parts)

    if user_info:
        messages.append({
            "role": "system",
            "content": _format_profile(user_info, "[SENDER PROFILE]: You are talking to the following user")
        })

    if other_users_info:
        for extra_user in other_users_info:
            messages.append({
                "role": "system",
                "content": _format_profile(extra_user, "[ADDITIONAL RELEVANT USER PROFILE]: Background on another user mentioned, replied to, or recently active in this chat")
            })
    
    # Add Recap if available
    if recap:
        messages.append({
            "role": "system", 
            "content": f"[CONVERSATION RECAP]: The following is a summary of the older parts of this conversation:\n{recap}"
        })

    # Add History (messages not summarized)
    if history:
        history_text = "\n".join([f"[{m['author']}]: {m['content']}" for m in history])
        messages.append({
            "role": "system",
            "content": f"[RECENT CHANNEL HISTORY]:\n{history_text}"
        })
    
    # Construct final user content with reply context integrated
    final_user_content = ""
    if reply_context:
        # Truncate reply context if too long
        if len(reply_context) > MAX_REPLY_CONTEXT_LENGTH:
            reply_context = reply_context[:MAX_REPLY_CONTEXT_LENGTH] + "..."
        final_user_content += f"### [REPLIED TO CONTEXT]:\n{reply_context}\n\n"

    final_user_content += f"### [USER PROMPT]:\n{user_prompt}\n### [USER PROMPT END]"
    
    messages.append({
        "role": "user",
        "content": final_user_content
    })
    
    return messages
