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
from ollama_client import ask_ollama, ask_ollama_json
from tools import search_web
from guardrails import is_safe_prompt
from database import init_db, add_reminder, get_due_reminders, delete_reminder, save_memory, get_memories, increment_stats, get_stats
import time
import urllib.parse
import json
import re
import os

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
        
class PromptQueue:
    def __init__(self, bot):
        self.bot = bot
        self.queue = asyncio.Queue()
        self.active_user_ids = set()  # Tracks users currently in queue or being processed
        self._current_user_id = None
        self.worker_task = None

    def start(self):
        if self.worker_task is not None and not self.worker_task.done():
            logger.info("PromptQueue worker already running.")
            return
        logger.info("Starting PromptQueue worker...")
        self.worker_task = asyncio.create_task(self._worker())

    async def put(self, user_id, message, loading_msg, user_prompt, reply_content, is_reply_to_self):
        if user_id in self.active_user_ids:
            logger.warning(f"User {user_id} already has an active task. Put rejected.")
            return False, 0
        
        self.active_user_ids.add(user_id)
        pos = self.queue.qsize() + (1 if self._current_user_id is not None else 0)
        
        logger.info(f"Adding task to queue for user {user_id} (Calculated Pos: {pos})")
        
        task_data = {
            "message": message,
            "loading_msg": loading_msg,
            "user_prompt": user_prompt,
            "reply_content": reply_content,
            "is_reply_to_self": is_reply_to_self
        }
        
        await self.queue.put((user_id, task_data))
        return True, pos

    async def _worker(self):
        logger.info("PromptQueue worker thread entered _worker loop.")
        while True:
            # Re-initialize these for every iteration to prevent scoping leak issues
            current_user_id = None
            current_task = None
            
            try:
                # Wait for next task
                user_id, task_data = await self.queue.get()
                current_user_id = user_id
                current_task = task_data
                self._current_user_id = user_id
                
                logger.info(f"PromptQueue: Processing task for user {user_id}")
                
                # Actual prompt processing happens here
                await self.bot.process_queued_prompt(
                    task_data["message"], 
                    task_data["loading_msg"], 
                    task_data["user_prompt"], 
                    task_data["reply_content"], 
                    task_data["is_reply_to_self"]
                )
                logger.info(f"PromptQueue: Successfully processed user {user_id}'s task.")
            except Exception as e:
                logger.error(f"PromptQueue: ERROR for user {current_user_id}: {e}", exc_info=True)
            finally:
                if current_user_id:
                    if current_user_id in self.active_user_ids:
                        self.active_user_ids.remove(current_user_id)
                    self._current_user_id = None
                    self.queue.task_done()
                    logger.info(f"PromptQueue: Finished cleanup for user {current_user_id}.")
                else:
                    logger.warning("PromptQueue: Worker iteration finished without a valid user_id.")

class GeminiSelfBot(discord.Client):
    def __init__(self, ollama_http_client: httpx.AsyncClient, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ollama_http_client = ollama_http_client
        self.start_time = int(time.time() * 1000)
        self.prompt_queue = PromptQueue(self)

    async def on_ready(self):
        # Set online status
        await self.change_presence(status=discord.Status.online)
        init_db()
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info("Self-bot is ready and listening for mentions/replies (Status: Online).")
        self.loop.create_task(self.reminder_loop())
        self.loop.create_task(status_loop(self))
        self.prompt_queue.start()

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

        # 5. Add to queue
        success, position = await self.prompt_queue.put(
            message.author.id, 
            message, 
            loading_msg, 
            user_prompt, 
            reply_content, 
            is_reply_to_self
        )

        if not success:
            await loading_msg.edit(content="> ❌ **Queue Full:** You already have an active prompt being processed or in the queue.")
            return

        if position > 0:
            await loading_msg.edit(content=f"> ⏳ ***Queued (Position #{position})...***")
            logger.info(f"on_message: User {message.author.id} queued at pos {position}")
        else:
            logger.info(f"on_message: User {message.author.id} added at pos 0 (Immediate Processing)")

    async def process_queued_prompt(self, message: discord.Message, loading_msg: discord.Message, user_prompt: str, reply_content: str, is_reply_to_self: bool):
        """
        Agentic loop allowing the main model to decide if it needs tools, thinking, or direct answer.
        """
        logger.info(f"process_queued_prompt: Starting agentic loop for user {message.author.id}")
        
        # Initial context
        messages = build_context(user_prompt, reply_content, is_reply_to_self)
        
        # Fetch memory context
        mems = get_memories(message.author.id)
        if mems:
            brain = "\n".join([f"- {m['key']}: {m['value']}" for m in mems])
            messages.insert(1, {"role": "system", "content": f"[User Facts & Memory]:\n{brain}"})
        
        # Tracking states
        think_enabled = "--think" in user_prompt.lower()
        
        # PROACTIVE SEARCH HEURISTIC
        # Force a search if the user asks about something recent or market-related
        search_keywords = ["lately", "recently", "current", "news", "crisis", "prices", "stock", "today", "now"]
        is_market_query = any(k in user_prompt.lower() for k in search_keywords)
        force_search = "--search" in user_prompt.lower() or is_market_query
        
        curr_phrases = PHRASES_DEFAULT
        status_task = None
        
        # --- AGENTIC REACT LOOP ---
        iteration = 0
        max_iterations = 4
        think_enabled = False
        curr_phrases = PHRASES_DEFAULT
        status_task = None
        executed_tools = set() # Track to avoid infinite loops
        
        while iteration < max_iterations:
            iteration += 1
            
            # Start/Restart rotation if needed
            if status_task: status_task.cancel()
            status_task = asyncio.create_task(rotate_status(loading_msg, curr_phrases))
            
            try:
                # 1. Call Ollama
                response = await ask_ollama(messages, think=think_enabled, client=self.ollama_http_client)
                
                if not response or response.strip() == "":
                    logger.error(f"Error in agent loop iteration {iteration}: Empty response from Ollama.")
                    # Fallback for Thinking Mode
                    if think_enabled:
                        response = "I have finished my reasoning process. How can I help you further?"
                    else:
                        break
                
                if response.startswith("Error: "):
                    logger.error(f"Ollama Error in loop: {response}")
                    await loading_msg.edit(content=f"⚠️ **Ollama Error:** {response}")
                    status_task.cancel()
                    return

                # 2. Forced Search (Iteration 1 only)
                if iteration == 1 and force_search:
                    logger.info("Force Search triggered by flag.")
                    await loading_msg.edit(content=f"> 🔍 ***{random.choice(PHRASES_SEARCH)}***")
                    curr_phrases = PHRASES_SEARCH
                    search_results = await search_web(user_prompt, client=self.ollama_http_client)
                    increment_stats(searches=1)
                    context = "\n".join([f"- {r['title']}: {r['snippet']}" for r in search_results.get('results', [])])
                    messages.append({"role": "system", "content": f"[Web Search Results]:\n{context}"})
                    force_search = False
                    continue

                # 3. Mode Switch: [MODE: think]
                if "[MODE: think]" in response and not think_enabled:
                    logger.info("Model requested Thinking Mode.")
                    think_enabled = True
                    curr_phrases = PHRASES_THINK
                    clean_resp = response.replace("[MODE: think]", "").strip()
                    if clean_resp:
                        messages.append({"role": "assistant", "content": clean_resp})
                    # Use a directive to guide the model
                    messages.append({"role": "system", "content": "[DIRECTIVE]: You are now in Thinking Mode. Provide a detailed, step-by-step reasoning chain before your final answer."})
                    continue

                # 4. Action: [ACTION: tool(args)]
                action_match = re.search(r'\[ACTION: (\w+)\((.*)\)\]', response)
                if action_match:
                    tool_name = action_match.group(1).lower()
                    tool_args = action_match.group(2)
                    tool_id = f"{tool_name}:{tool_args}"
                    
                    if tool_id in executed_tools:
                        logger.warning(f"Model repeated tool call: {tool_id}. Breaking loop.")
                        break
                        
                    logger.info(f"Model triggered tool: {tool_name}({tool_args})")
                    executed_tools.add(tool_id)
                    
                    tool_result = await self._dispatch_tool(tool_name, tool_args, message, loading_msg)
                    
                    if tool_result == "HANDLED_UI":
                        status_task.cancel()
                        return
                        
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": f"[TOOL_RESULT]: {tool_result}"})
                    curr_phrases = PHRASES_ACTION if tool_name != "search" else PHRASES_SEARCH
                    continue

                # 5. Final Response
                status_task.cancel()
                await self._send_safe_response(loading_msg, response, message)
                increment_stats(messages=1)
                return

            except Exception as e:
                if status_task: status_task.cancel()
                logger.error(f"Error in agent loop iteration {iteration}: {e}")
                try:
                    await loading_msg.edit(content=f"⚠️ **Error:** {str(e)[:1800]}")
                except:
                    pass
                return

    async def _send_safe_response(self, loading_msg: discord.Message, content: str, original_msg: discord.Message):
        """Helper to send responses that might exceed Discord's 2000 character limit, prioritizing newline splits."""
        if len(content) <= 2000:
            await loading_msg.edit(content=content)
            return

        chunks = []
        remaining = content
        while len(remaining) > 0:
            if len(remaining) <= 1900:
                chunks.append(remaining)
                break
            
            # Find the best split point (last newline before limit)
            split_idx = remaining.rfind('\n', 0, 1900)
            if split_idx == -1:
                # Fallback: split at last space
                split_idx = remaining.rfind(' ', 0, 1900)
            if split_idx == -1:
                # Fallback: hard split
                split_idx = 1900
                
            chunks.append(remaining[:split_idx].strip())
            remaining = remaining[split_idx:].strip()

        # Edit the first chunk into the loading message
        if chunks:
            await loading_msg.edit(content=chunks[0])
        
        # Send subsequent chunks as new messages
        for i in range(1, len(chunks)):
            if chunks[i].strip():
                await original_msg.channel.send(chunks[i])
                await asyncio.sleep(0.8) # Slightly longer delay for multi-message clarity

    async def _dispatch_tool(self, name: str, args: str, message: discord.Message, loading_msg: discord.Message) -> str:
        """Helper to execute tools and return a string result for the LLM."""
        try:
            # Clean quotes from args
            clean_args = args.strip(' "\'')
            
            if name == "search":
                await loading_msg.edit(content=f"> 🔍 ***{random.choice(PHRASES_SEARCH)}***")
                res = await search_web(clean_args, client=self.ollama_http_client)
                increment_stats(searches=1)
                if res.get("status") == "success":
                    return "\n".join([f"- {r['title']}: {r['snippet']}" for r in res['results']])
                return f"Search failed: {res.get('message', 'Unknown Error')}"
                
            elif name == "calculate":
                from tools import calculate_math
                res = await calculate_math(clean_args)
                increment_stats(tools=1)
                return f"Result: {res['result']}" if res['status'] == "success" else f"Math Error: {res['message']}"
                
            elif name == "weather":
                async with httpx.AsyncClient(timeout=10.0) as wc:
                    resp = await wc.get(f"https://wttr.in/{urllib.parse.quote(clean_args)}?format=3")
                    increment_stats(tools=1)
                    return resp.text.strip() if resp.status_code == 200 else "Weather service unavailable."
                    
            elif name == "reminder":
                # Expecting format: seconds, "topic"
                parts = [p.strip(' "') for p in clean_args.split(",")]
                secs = int(parts[0]) if parts[0].isdigit() else 60
                topic = parts[1] if len(parts) > 1 else "Reminder"
                trigger = int(time.time()) + secs
                add_reminder(message.channel.id, message.id, trigger, topic)
                await loading_msg.edit(content=f"> ⏰ **Timer Set:** I will remind you accurately in {secs} seconds.")
                increment_stats(tools=1, messages=1)
                return "HANDLED_UI"
                
            elif name == "memory_save":
                # Expecting format: "key", "value"
                parts = [p.strip(' "') for p in clean_args.split(",")]
                key = parts[0] if parts[0] else "note"
                val = parts[1] if len(parts) > 1 else ""
                save_memory(message.author.id, key, val)
                await loading_msg.edit(content=f"> 🧠 **Memory Saved:** I've taken a note of that.")
                increment_stats(tools=1, messages=1)
                return "HANDLED_UI"
                
            elif name == "summarize":
                limit = int(clean_args) if clean_args.isdigit() else 50
                history = [msg async for msg in message.channel.history(limit=limit, before=message) if msg.author.id != self.user.id]
                transcript = "\n".join([f"{m.author.name}: {m.content}" for m in reversed(history)])
                return f"Transcribed History:\n{transcript}"
                
            elif name == "stats":
                stats = get_stats()
                dashboard = (
                    f"> 📊 **Global Bot Statistics**\n"
                    f"> 💬 **Messages Answered:** `{stats.get('messages_answered', 0)}`\n"
                    f"> 🔋 **Tokens Consumed:** `{stats.get('tokens_used', 0)}`\n"
                    f"> 🔍 **Deep Searches Run:** `{stats.get('searches_run', 0)}`\n"
                    f"> 🧰 **Tools Executed:** `{stats.get('tools_used', 0)}`"
                )
                await loading_msg.edit(content=dashboard)
                increment_stats(tools=1, messages=1)
                return "HANDLED_UI"
                
            return f"Error: Tool '{name}' not found."
        except Exception as e:
            return f"Error executing tool {name}: {str(e)}"
