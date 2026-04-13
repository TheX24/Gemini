import re
import config
from config import MAX_REPLY_CONTEXT_LENGTH, SPICY_LYRICS_KNOWLEDGE_FILE, SPICY_LYRICS_EXAMPLES_DIR
import pathlib
import base64

def clean_mention(content: str, user_id: int) -> str:
    """
    Remove the self-bot's mention from the message and normalize whitespace.
    """
    # Regex for user mention in Discord (e.g., <@123456789> or <@!123456789>)
    mention_pattern = rf"<@!?{user_id}>"
    cleaned = re.sub(mention_pattern, "", content)
    
    # Trim and normalize extra whitespace
    return " ".join(cleaned.split()).strip()

def is_spicy_query(user_prompt: str, history: list | None = None) -> bool:
    """
    Check if the user prompt or recent history suggests a need for Spicy Lyrics knowledge.
    """
    spicy_keywords = [
        "spicy", "lyrics", "ttml", "sync", "upload", "kawarp", 
        "spicetify", "spotify id", "formatting", "genius", 
        "musixmatch", "apple music", "mod", "extension", "guide"
    ]
    
    text_to_check = user_prompt.lower()
    if history:
        # Check last 3 messages for context
        history_text = " ".join([m['content'].lower() for m in history[-3:]])
        text_to_check += " " + history_text
        
    return any(keyword in text_to_check for keyword in spicy_keywords)

def build_context(user_prompt: str, reply_context: str | None = None, is_reply_to_self: bool = True, history: list | None = None, recap: str | None = None, user_info: dict | None = None, other_users_info: list | None = None, bot_username: str | None = None, media_data: list[dict] | None = None, variation: str = "default") -> list:
    """
    Construct the final list of messages for Ollama with history and optional recap.
    Includes direct multimodal support for images, video, and audio.
    """
    import datetime
    import base64
    now_str = datetime.datetime.now().strftime("%A, %B %d, %Y, %H:%M:%S")
    
    messages = [
        {"role": "system", "content": config.get_system_prompt(variation)},
    ]


    # 1. Inject Spicy Lyrics Knowledge Base & Examples (CONDITIONAL)
    if is_spicy_query(user_prompt, history):
        if SPICY_LYRICS_KNOWLEDGE_FILE and SPICY_LYRICS_KNOWLEDGE_FILE.exists():
            try:
                kb_content = SPICY_LYRICS_KNOWLEDGE_FILE.read_text(encoding="utf-8").strip()
                messages.append({"role": "system", "content": f"[SPICY LYRICS KNOWLEDGE]:\n{kb_content}"})
            except Exception as e:
                print(f"Error loading knowledge base: {e}")
                
        if SPICY_LYRICS_EXAMPLES_DIR and SPICY_LYRICS_EXAMPLES_DIR.exists():
            try:
                for ttml_file in SPICY_LYRICS_EXAMPLES_DIR.glob("*.ttml"):
                    example_content = ttml_file.read_text(encoding="utf-8").strip()
                    messages.append({"role": "system", "content": f"[SPICY LYRICS EXAMPLE - {ttml_file.name}]:\n{example_content}"})
            except Exception as e:
                print(f"Error loading examples: {e}")
    
    # 2. Inject Time Context
    messages.append({"role": "system", "content": f"[Time Context]: The current date and time is {now_str}."})

    def _format_profile(info: dict, label_str: str):
        profile_parts = [
            f"Display Name: {info.get('display_name')}",
            f"Username: {info.get('username')}",
        ]

        # Global display name (if different from username)
        global_name = info.get('global_name')
        if global_name:
            profile_parts.append(f"Global Name: {global_name}")

        profile_parts += [
            f"ID: {info.get('id')}",
            f"Account Created: {info.get('created_at')}",
            f"Server: {info.get('server_name')}",
        ]

        # Server-specific fields (only present for Members)
        nick = info.get('server_nickname')
        if nick:
            profile_parts.append(f"Server Nickname: {nick}")

        joined = info.get('joined_server_at')
        if joined:
            profile_parts.append(f"Joined Server: {joined}")

        top_role = info.get('top_role')
        if top_role:
            profile_parts.append(f"Top Role: {top_role}")

        roles = info.get('server_roles')
        if roles:
            profile_parts.append(f"Server Roles: {', '.join(roles)}")

        role_colour = info.get('role_colour')
        if role_colour:
            profile_parts.append(f"Role Colour: {role_colour}")

        booster = info.get('server_booster_since')
        if booster:
            profile_parts.append(f"Server Booster Since: {booster}")

        timed_out = info.get('timed_out_until')
        if timed_out:
            profile_parts.append(f"⚠️ Timed Out Until: {timed_out}")

        perms = info.get('guild_permissions')
        if perms:
            flagged = [k.replace('_', ' ').title() for k, v in perms.items() if v]
            if flagged:
                profile_parts.append(f"Notable Permissions: {', '.join(flagged)}")

        # Online presence
        online = info.get('online_status')
        if online:
            profile_parts.append(f"Online Status: {online}")
        for device in ('desktop_status', 'mobile_status', 'web_status'):
            val = info.get(device)
            if val and val != 'offline':
                profile_parts.append(f"{device.replace('_', ' ').title()}: {val}")

        # Activities / Rich Presence
        activities = info.get('activities') or info.get('status')   # handle old key gracefully
        if activities:
            profile_parts.append(f"Activities: {', '.join(activities)}")

        # Profile / Nitro
        bio = info.get('bio')
        if bio:
            profile_parts.append(f"About Me (Bio): {bio}")

        pronouns = info.get('pronouns')
        if pronouns:
            profile_parts.append(f"Pronouns: {pronouns}")

        # Nitro — prefer new keys, fall back to old key
        nitro_since = info.get('nitro_since') or info.get('premium_since')
        nitro_type  = info.get('nitro_type')
        if nitro_since:
            label = f"Nitro ({nitro_type})" if nitro_type and nitro_type != "None" else "Nitro"
            profile_parts.append(f"{label} since: {nitro_since}")

        banner = info.get('banner_url')
        if banner:
            profile_parts.append(f"Banner: {banner}")

        accent = info.get('accent_colour')
        if accent:
            profile_parts.append(f"Accent Colour: {accent}")

        avatar = info.get('avatar_url')
        if avatar:
            profile_parts.append(f"Avatar: {avatar}")

        connections = info.get('connections')
        if connections:
            profile_parts.append(f"Connected Accounts: {', '.join(connections)}")

        mutual_guilds = info.get('mutual_guild_count')
        if mutual_guilds is not None:
            profile_parts.append(f"Mutual Servers: {mutual_guilds}")

        mutual_friends = info.get('mutual_friend_count')
        if mutual_friends is not None:
            profile_parts.append(f"Mutual Friends: {mutual_friends}")

        recent = info.get('recent_activity')
        if recent and recent != "None":
            profile_parts.append(f"Recent Activity: {recent}")

        game_board = info.get('game_leaderboard') or info.get('game_board')
        if game_board and game_board != "None":
            profile_parts.append(f"Game Leaderboards: {game_board}")

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
    # Refactored: Interleave as User/Assistant roles for native memory ownership
    if history:
        for m in history:
            author = m.get('author')
            content = m.get('content', '')
            
            # Determine role: Is this the bot talking?
            # Check against bot_username if provided, otherwise fallback to name-based heuristic
            is_bot = False
            if bot_username and author == bot_username:
                is_bot = True
            elif author.lower() in ("gemini", "assistant", "bot"):
                is_bot = True
                
            if is_bot:
                messages.append({"role": "assistant", "content": content})
            else:
                # For users, prefix with username to maintain group chat context
                messages.append({"role": "user", "content": f"[{author}]: {content}"})

    
    # Construct final user content with reply context integrated
    final_user_content = ""
    if reply_context:
        # Truncate reply context if too long
        if len(reply_context) > MAX_REPLY_CONTEXT_LENGTH:
            reply_context = reply_context[:MAX_REPLY_CONTEXT_LENGTH] + "..."
        final_user_content += f"### [REPLIED TO CONTEXT]:\n{reply_context}\n\n"

    final_user_content += f"### [USER PROMPT]:\n{user_prompt}\n### [USER PROMPT END]"
    
    if media_data:
        media_names = [item.get("filename", "unknown_file") for item in media_data]
        final_user_content += f"\n\n### [ATTACHED MEDIA]:\nThe user attached the following media files to this message (provided as inlineData): {', '.join(media_names)}"
    

    # SYSTEM SANDWICH: Reinforce tool execution protocol at the very end of context
    messages.append({
        "role": "system",
        "content": (
            "[ACTION REQUIRED]: If you need a tool, output exactly: [ACTION: tool_name(\"args\")]. "
            "To use thinking mode, start with [MODE: think]. Always balance reasoning with direct tool usage."
        )
    })

    user_message = {
        "role": "user",
        "content": final_user_content
    }
    
    if media_data:
        user_message["media"] = [
            {
                "mime_type": item["mime_type"], 
                "data": base64.b64encode(item["data"]).decode("utf-8")
            } for item in media_data
        ]

    messages.append(user_message)
    return messages
