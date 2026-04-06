import discord
import logging
import asyncio
import httpx
import random
from config import (
    OLLAMA_MODEL, PHRASES_DEFAULT, PHRASES_THINK, 
    PHRASES_SEARCH, PHRASES_HYBRID, PHRASES_ACTION
)
from context_builder import clean_mention, build_context
from router import classify_request_llm
from ollama_client import ask_ollama, ask_ollama_json
from tools import search_web
from guardrails import is_safe_prompt
from database import init_db, add_reminder, get_due_reminders, delete_reminder, save_memory, get_memories, increment_stats, get_stats
import time

# Set up logging for the bot
logger = logging.getLogger(__name__)

async def rotate_status(loading_msg: discord.Message, phrases: list):
    """
    Background task to rotate loading phrases if generation takes a while.
    """
    used_phrases = set()
    try:
        while True:
            # Wait before changing the message. 
            # This ensures the initial phrase stays on screen for a bit, 
            # and spaces out subsequent rotations nicely.
            await asyncio.sleep(random.uniform(6, 10))
            
            # Pick a new phrase not just used
            available = [p for p in phrases if p not in used_phrases]
            if not available:
                used_phrases.clear()
                available = phrases
                
            phrase = random.choice(available)
            used_phrases.add(phrase)
            await loading_msg.edit(content=f"> ⏳ ***{phrase}***")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in status rotation: {e}")

async def status_loop(bot: discord.Client):
    """
    Background task to continuously update the bot's Rich Presence with global stats.
    """
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            stats = get_stats()
            servers = len(bot.guilds)
            msgs = stats['messages_answered']
            tokens = stats['tokens_used']
            
            # Format nicely, e.g., 25.5k
            tok_str = f"{tokens/1000:.1f}k" if tokens > 1000 else str(tokens)
            
            # Send raw protocol-level Presence update to force-render the icon
            # This bypasses library filtering that strips assets from user accounts
            payload = {
                "op": 3, # PRESENCE_UPDATE
                "d": {
                    "since": 0,
                    "activities": [{
                        "name": "Answering questions", 
                        "type": 5, # Playing (0) or Competing (5)
                        "details": f"🧠 Connected to {servers} Servers",
                        "state": f"💬 {msgs} Answered | 🔋 {tok_str} Tokens",
                        "application_id": str(1250551199862624349), 
                        "assets": {
                            "large_image": "1490776461685162104", 
                        },
                        "timestamps": {
                            "start": int(bot.start_time)
                        }
                    }],
                    "status": "online",
                    "afk": False
                }
            }
            await bot.ws.send_as_json(payload)
        except Exception as e:
            logger.error(f"Failed to update rich presence: {e}")
        
        await asyncio.sleep(60)

class GeminiSelfBot(discord.Client):
    def __init__(self, ollama_http_client: httpx.AsyncClient, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ollama_http_client = ollama_http_client
        self.start_time = int(time.time() * 1000)

    async def on_ready(self):
        # Set online status
        await self.change_presence(status=discord.Status.online)
        init_db()
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info("Self-bot is ready and listening for mentions/replies (Status: Online).")
        self.loop.create_task(self.reminder_loop())
        self.loop.create_task(status_loop(self))

    async def reminder_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                due = get_due_reminders()
                for r in due:
                    try:
                        channel = self.get_channel(r['channel_id']) or await self.fetch_channel(r['channel_id'])
                        if channel:
                            msg = await channel.fetch_message(r['message_id'])
                            await msg.reply(f"> ⏰ **Reminder:** {r['topic']}")
                        delete_reminder(r['id'])
                    except Exception as e:
                        logger.error(f"Failed to send reminder {r['id']}: {e}")
            except Exception as e:
                logger.error(f"Error in reminder_loop: {e}")
            
            await asyncio.sleep(10)

    async def on_message(self, message: discord.Message):
        # 1. Ignore messages from yourself (to prevent infinite loops)
        if message.author.id == self.user.id:
            return
            
        # 2. Ignore messages from other bots
        if message.author.bot:
            return

        # 3. Check for triggers: Direct Mention or Reply to self
        is_mentioned = False
        if any(mention.id == self.user.id for mention in message.mentions):
            is_mentioned = True
            
        # Also trigger if they explicitly type @gemini or gemini
        if "@gemini" in message.content.lower() or "gemini," in message.content.lower():
            is_mentioned = True
            
        is_reply_to_self = False
        reply_content = None
        
        if message.reference:
            try:
                # Resolve the referenced message
                ref_msg = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
                if ref_msg:
                    if ref_msg.author.id == self.user.id:
                        is_reply_to_self = True
                        reply_content = ref_msg.content
                    else:
                        reply_content = f"{ref_msg.author.name} said: {ref_msg.content}"
            except discord.HTTPException:
                pass

        if not (is_mentioned or is_reply_to_self):
            return

        # 4. Process the message
        logger.info(f"Triggered by {message.author}: '{message.content}'")
        
        # Clean the input
        user_prompt = clean_mention(message.content, self.user.id)
        if not user_prompt and not is_reply_to_self:
            return

        # Core Safety Guardrail
        # Deny obvious malicious patterns immediately to save compute
        is_safe, refusal_reason = is_safe_prompt(user_prompt)
        if not is_safe:
            logger.warning(f"Guardrail blocked request from {message.author}: {refusal_reason}")
            await message.reply(f"> 🛡️ **Guardrail Triggered:** {refusal_reason}\nI cannot fulfill this request.")
            return

        # Display initial loading state immediately
        loading_msg = await message.reply("> ⏳ ***Parsing intent...***")

        # Route the request using the separate router model
        classification = await classify_request_llm(user_prompt, reply_content, self.ollama_http_client)
        route = classification.get("route", "fast")
        reason = classification.get("reason", "No reason provided")
        
        logger.info(f"Routing to: {route} (Reason: {reason})")

        # Pick phrase set based on route
        if route == "search_think":
            curr_phrases = PHRASES_HYBRID
        elif route == "search":
            curr_phrases = PHRASES_SEARCH
        elif route == "think":
            curr_phrases = PHRASES_THINK
        elif route == "action":
            curr_phrases = PHRASES_ACTION
        else:
            curr_phrases = PHRASES_DEFAULT

        # Change to the first real phrase now that intent is parsed
        initial_phrase = random.choice(curr_phrases)
        await loading_msg.edit(content=f"> ⏳ ***{initial_phrase}***")
        
        # Start rotation task
        status_task = asyncio.create_task(rotate_status(loading_msg, curr_phrases))
        
        # Optionally trigger typing
        async with message.channel.typing():
            try:
                if route == "action":
                    tool = classification.get("tool", "none")
                    
                    if tool == "reminder":
                        secs = classification.get("time_seconds") or 60
                        topic = classification.get("topic") or "Reminder"
                        trigger = int(time.time()) + secs
                        add_reminder(message.channel.id, message.id, trigger, topic)
                        await loading_msg.edit(content=f"> ⏰ **Timer Set:** I will remind you accurately in {secs} seconds.")
                        increment_stats(tools=1, messages=1)
                        status_task.cancel()
                        return
                        
                    elif tool == "memory_save":
                        save_memory(message.author.id, classification.get("topic") or "note", classification.get("value") or "")
                        await loading_msg.edit(content=f"> 🧠 **Memory Saved:** I've taken a note of that.")
                        increment_stats(tools=1, messages=1)
                        status_task.cancel()
                        return
                        
                    elif tool == "summarize":
                        limit = classification.get("message_count") or 50
                        history = [msg async for msg in message.channel.history(limit=limit, before=message) if msg.author.id != self.user.id]
                        transcript = "\n".join([f"{m.author.name}: {m.content}" for m in reversed(history)])
                        sm = [{"role": "system", "content": "Summarize the following chat context clearly."}, {"role": "user", "content": transcript}]
                        summary = await ask_ollama(sm, client=self.ollama_http_client)
                        await loading_msg.edit(content=summary)
                        increment_stats(tools=1, messages=1)
                        status_task.cancel()
                        return
                        
                    elif tool == "calculate":
                        from tools import calculate_math
                        expr = classification.get("expression") or ""
                        res = await calculate_math(expr)
                        if res["status"] == "success":
                            await loading_msg.edit(content=f"> 🔢 **Calculation:** `{res['expression']}` = **{res['result']}**")
                        else:
                            await loading_msg.edit(content=f"> ❌ **Math Error:** {res['message']}")
                        increment_stats(tools=1, messages=1)
                        status_task.cancel()
                        return
                            
                    elif tool == "translate":
                        text = classification.get("text") or ""
                        target = classification.get("target_lang") or "English"
                        # Use LLM to perform the translation as defined in tools.py
                        sm = [{"role": "system", "content": f"You are a professional translator. Translate to {target}. Output ONLY the translated text."}, {"role": "user", "content": text}]
                        translation = await ask_ollama(sm, client=self.ollama_http_client)
                        await loading_msg.edit(content=f"> 🌍 **Translation ({target}):**\n{translation}")
                        increment_stats(tools=1, messages=1)
                        status_task.cancel()
                        return
                        
                    elif tool == "weather":
                        loc = classification.get("location") or ""
                        # Use a clean client specifically for the weather to avoid base_url/config issues
                        async with httpx.AsyncClient(timeout=10.0) as weather_client:
                            # URL encode the location for cities with spaces like "New York"
                            import urllib.parse
                            encoded_loc = urllib.parse.quote(loc)
                            try:
                                resp = await weather_client.get(f"https://wttr.in/{encoded_loc}?format=3")
                                if resp.status_code == 200 and resp.text:
                                    weather_data = resp.text.strip()
                                    await loading_msg.edit(content=f"> ⛅ **Weather:** {weather_data}")
                                else:
                                    logger.warning(f"wttr.in returned {resp.status_code}")
                                    await loading_msg.edit(content="> ⛅ **Weather Error:** Local weather service is temporarily unavailable.")
                            except Exception as e:
                                logger.error(f"Weather HTTP error: {e}")
                                await loading_msg.edit(content="> ⛅ **Weather Error:** Could not connect to the weather provider.")
                        
                        increment_stats(tools=1, messages=1)
                        status_task.cancel()
                        return
                        
                    elif tool == "stats":
                        stats = get_stats()
                        servers = len(self.guilds)
                        dashboard = (
                            f"> 📊 **Global Bot Statistics**\n"
                            f"> 💬 **Messages Answered:** `{stats.get('messages_answered', 0)}`\n"
                            f"> 🔋 **Tokens Consumed:** `{stats.get('tokens_used', 0)}`\n"
                            f"> 🔍 **Deep Searches Run:** `{stats.get('searches_run', 0)}`\n"
                            f"> 🧰 **Tools Executed:** `{stats.get('tools_used', 0)}`\n"
                            f"> 👁️ **Servers Monitored:** `{servers}`"
                        )
                        await loading_msg.edit(content=dashboard)
                        increment_stats(tools=1, messages=1)
                        status_task.cancel()
                        return

                    else:
                        # SILENT FALLBACK: If tool is unknown, treat as a conversation
                        logger.warning(f"Unrecognized tool '{tool}' for action route. Falling back to conversation.")
                        route = "fast"
                        # Do NOT return, let it fall through to the conversation logic below
                # Build context
                messages = build_context(user_prompt, reply_content, is_reply_to_self)
                
                # Fetch memory context for standard queries
                mems = get_memories(message.author.id)
                if mems:
                    brain = "\n".join([f"- {m['key']}: {m['value']}" for m in mems])
                    messages.insert(1, {"role": "system", "content": f"[User Facts & Notes Database]:\n{brain}"})
                
                # Handle Search route
                if "search" in route:
                    sq = classification.get("search_query") or user_prompt
                    search_results = await search_web(sq, client=self.ollama_http_client)
                    increment_stats(searches=1)
                    # Inject search results into the prompt context
                    search_context = ""
                    if search_results.get("status") == "success":
                        search_context = "\n".join([f"- {r['title']}: {r['snippet']}" for r in search_results['results']])
                    else:
                        search_context = search_results.get('message', 'Search failed.')
                        
                    messages.insert(1, {
                        "role": "system", 
                        "content": f"[Web Search Results]:\n{search_context}"
                    })
                
                # Call Ollama
                think_enabled = "think" in route
                response_text = await ask_ollama(messages, think=think_enabled, client=self.ollama_http_client)
                
                # Cancel rotation before editing
                status_task.cancel()
                
                # Edit the loading message with the final response
                if response_text:
                    await loading_msg.edit(content=response_text)
                    increment_stats(messages=1)
                else:
                    await loading_msg.edit(content="Error: No response from assistant.")
            except Exception as e:
                status_task.cancel()
                logger.error(f"Error processing message: {e}")
                await loading_msg.edit(content=f"An error occurred: {str(e)}")
