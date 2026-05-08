import discord
import logging
import asyncio
import httpx
import random
import subprocess
import config
# Constants
PHRASES_DEFAULT = config.PHRASES_DEFAULT
PHRASES_THINK = config.PHRASES_THINK
PHRASES_SEARCH = config.PHRASES_SEARCH
PHRASES_HYBRID = config.PHRASES_HYBRID
PHRASES_ACTION = config.PHRASES_ACTION
PHRASES_PARSING = config.PHRASES_PARSING
PHRASES_QUEUE = config.PHRASES_QUEUE

# Error Mapping for LLM Responses
LLM_ERROR_MAPPING = {
    400: ("Invalid Argument", "Something went a bit sideways with that request! I'm having trouble processing the details as they were sent."),
    401: ("Unauthenticated", "I'm having trouble logging in to my AI brain. It looks like my API key is missing or invalid. Please alert my owner!"),
    403: ("Permission Denied", "I'm not allowed to do that! It seems I don't have the right permissions to access this specific resource."),
    404: ("Not Found", "I can't find the model I'm supposed to use. It might have been retired or moved. I should probably check my settings!"),
    408: ("Request Timeout", "The request took too long and timed out. My connection might be a bit unstable right now."),
    429: ("Resource Exhausted", "I'm a bit overwhelmed right now! I've hit my rate limit or the servers are too busy. Please give me a minute to breathe and try again."),
    500: ("Internal Error", "Oops! Something went wrong on the AI's side. Google's servers are having a bit of a hiccup. Let's try again in a moment."),
    502: ("Bad Gateway", "I'm having trouble communicating with the AI servers. There's a bit of a bridge out on the information superhighway!"),
    503: ("Service Unavailable", "The AI service is currently down for maintenance or is heavily overloaded. It's not you, it's them! Check back shortly."),
    504: ("Gateway Timeout", "The AI took too long to respond. It's likely stuck on a very complex thought. Try a simpler prompt or wait a bit."),
}

from context_builder import clean_mention, build_context
from llm_client import ask_llm, extract_error_code
from gemini_client import get_client, types

from guardrails import is_safe_prompt
from database import (
    init_db, add_reminder, get_due_reminders, delete_reminder, save_memory, 
    get_memories, increment_stats, get_stats, save_user_variation, 
    get_user_settings, get_message_variation, save_message_variation,
    get_channel_settings, save_channel_variation, get_server_settings,
    save_server_variation,
    save_system_state, get_system_state, save_keyword_memory, get_keyword_memories,
    add_to_whitelist, remove_from_whitelist, is_whitelisted, toggle_whitelist,
    get_budget_spent, add_to_budget_spent
)
import time
import urllib.parse
import json
import re
import os
import io

# ---------------------------------------------------------------------------
# File attachment security policy
# ---------------------------------------------------------------------------
# Only these plain-text extensions are allowed.  Everything else is blocked.
# Hard block list – binaries, compiled code, or containers.
# This ensures the bot doesn't attempt to read non-text data.
BLOCKED_EXTENSIONS: set[str] = {
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear",
    ".exe", ".dll", ".so", ".dylib", ".elf", ".bin", ".out", ".run",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".iso", ".img", ".dmg",
}

async def read_attachments(attachments: list[discord.Attachment]) -> tuple[list[dict], str | None]:
    """
    Download and validate all attachments from a Discord message.
    
    Returns:
        (list_of_media_dicts, error_message)
    """
    import mimetypes
    if not attachments:
        return [], None

    # Respect the per-message file cap
    attachments = attachments[:config.MAX_ATTACHMENT_COUNT]

    results: list[dict] = []
    for att in attachments:
        ext = os.path.splitext(att.filename)[1].lower()

        # Block executables / binaries first (deny always wins)
        if ext in BLOCKED_EXTENSIONS:
            return [], (
                f"🚫 **Blocked:** `{att.filename}` has a potentially executable extension (`{ext}`). "
                "I cannot read files that could be executed for security reasons."
            )

        # Size guard
        if att.size > config.MAX_MEDIA_BYTES:
            size_mb = att.size / 1_000_000
            return [], (
                f"❌ **File too large:** `{att.filename}` is {size_mb:.1f} MB. "
                f"Maximum size per file is {config.MAX_MEDIA_BYTES // 1_000_000} MB."
            )

        # Download the file content
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(att.url)
                resp.raise_for_status()
                data = resp.content
                
                mime_type, _ = mimetypes.guess_type(att.filename)
                if getattr(att, "is_voice_message", lambda: False)():
                    mime_type = "audio/ogg"
                
                # Expand guessing for Gemini-friendly types and avoid application/octet-stream
                if not mime_type or mime_type == "application/octet-stream":
                    # Text/Code fallbacks
                    if ext in [
                        ".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".cpp", ".cc", ".h", ".hpp", 
                        ".cs", ".go", ".rs", ".md", ".markdown", ".json", ".yaml", ".yml", 
                        ".toml", ".sql", ".sh", ".bash", ".zsh", ".env", ".log", ".txt"
                    ]:
                        mime_type = "text/plain"
                    elif ext == ".pdf":
                        mime_type = "application/pdf"
                    else:
                        # Gemini rejects application/octet-stream for inline data.
                        # Defaulting to text/plain is safer; if it's actually binary, 
                        # the model will just see it as encoded characters.
                        mime_type = "text/plain"
                
                results.append({
                    "filename": att.filename,
                    "mime_type": mime_type,
                    "data": data,
                    "size": att.size
                })
        except Exception as e:
            return [], f"❌ **Failed to download** `{att.filename}`: {e}"

    return results, None

# Set up logging for the bot
logger = logging.getLogger(__name__)

async def rotate_status(loading_msg: discord.Message | None, phrases: list, prefix: str = "> ⏳ ***", original_msg: discord.Message = None):
    """
    Background task to rotate loading phrases if generation takes a while.
    When config.SHOW_LOADING_MESSAGES is disabled, uses typing indicator on original_msg instead.
    """
    if not config.SHOW_LOADING_MESSAGES:
        try:
            if original_msg:
                async with original_msg.channel.typing():
                    while True:
                        await asyncio.sleep(3600)
            else:
                while True:
                    await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        return

    used_phrases = set()
    try:
        while True:
            # Wait before changing the message (wait less if we have many many phrases)
            sleep_time = random.uniform(5, 8) if len(phrases) < 10 else random.uniform(4, 6)
            await asyncio.sleep(sleep_time)
            
            # Pick a new phrase not just used
            available = [p for p in phrases if p not in used_phrases]
            if not available:
                used_phrases.clear()
                available = phrases
                
            phrase = random.choice(available)
            used_phrases.add(phrase)
            
            # CRITICAL: Do not edit if we were cancelled during the sleep
            # This prevents "zombie" edits from overwriting the final answer.
            try:
                await loading_msg.edit(content=f"{prefix}{phrase}***")
            except discord.NotFound:
                break # Message deleted, stop rotating
            except Exception:
                pass # Ignore transient edit errors
    except asyncio.CancelledError:
        # Task was cancelled, exit quietly
        pass
    except Exception as e:
        logger.error(f"Error in status rotation: {e}")

async def safe_cancel_status(task: asyncio.Task | None):
    """Safely cancels and awaits a status rotation task to prevent race conditions."""
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def status_loop(bot: discord.Client):
    """
    Background task to continuously update the bot's Rich Presence with global stats.
    """
    while not bot.is_closed():
        try:
            await bot.wait_until_ready()
            
            stats = get_stats()
            servers = len(bot.guilds)
            msgs = stats.get('messages_answered', 0) or 0
            tokens = stats.get('tokens_used', 0) or 0
            
            # Format nicely, e.g., 25.5k, 1.25M
            if tokens >= 1_000_000:
                tok_str = f"{tokens/1_000_000:.2f}M"
            elif tokens >= 1000:
                tok_str = f"{tokens/1000:.1f}k"
            else:
                tok_str = str(tokens)
            
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
            
            # Safely check for websocket before sending
            ws = getattr(bot, 'ws', None)
            if ws and hasattr(ws, 'send_as_json'):
                try:
                    await ws.send_as_json(payload)
                except Exception as ws_err:
                    # Specific workaround for 'closed' attribute error in some library versions
                    if "'closed'" in str(ws_err):
                        await ws.send(json.dumps(payload))
                    else:
                        raise ws_err
            else:
                logger.warning("Websocket not available for rich presence update.")
        except Exception as e:
            logger.error(f"Failed to update rich presence: {e}")
        
        # Run every 5 minutes (300s) to reduce gateway noise
        await asyncio.sleep(300)
        
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

    async def put(self, user_id, message, loading_msg, user_prompt, reply_content, is_reply_to_self, history=None, user_info=None, other_users_info=None, attachments_text=None, media_data=None, status_data=None):
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
            "is_reply_to_self": is_reply_to_self,
            "history": history or [],
            "user_info": user_info or {},
            "other_users_info": other_users_info,
            "attachments_text": attachments_text,
            "media_data": media_data,
            "status_data": status_data, # This is a dict/ref: {"task": <asyncio.Task>}
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
                    task_data["is_reply_to_self"],
                    task_data["history"],
                    task_data["user_info"],
                    task_data.get("other_users_info"),
                    task_data.get("attachments_text"),
                    task_data.get("media_data"),
                    task_data.get("status_data")
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

async def extract_user_metadata(user: discord.User | discord.Member, guild: discord.Guild | None) -> dict:
    """
    Extract as much publicly available metadata as possible for a Discord user/member.
    Handles both discord.User (DMs / minimal info) and discord.Member (full guild context).
    """
    # ── Base identity ────────────────────────────────────────────────────────
    user_info: dict = {
        "display_name":  getattr(user, "display_name", str(user)),
        "username":      str(user),
        "global_name":   getattr(user, "global_name", None),
        "id":            user.id,
        "bot":           user.bot,
        "system":        getattr(user, "system", False),
        "created_at":    user.created_at.strftime("%Y-%m-%d %H:%M UTC"),
        "avatar_url":    str(user.display_avatar.url) if user.display_avatar else None,
    }

    # ── Online presence ──────────────────────────────────────────────────────
    raw_status = getattr(user, "status", None)
    if raw_status is not None:
        user_info["online_status"] = str(raw_status)          # online / idle / dnd / offline
    raw_mobile = getattr(user, "mobile_status", None)
    if raw_mobile is not None:
        user_info["mobile_status"] = str(raw_mobile)
    raw_desktop = getattr(user, "desktop_status", None)
    if raw_desktop is not None:
        user_info["desktop_status"] = str(raw_desktop)
    raw_web = getattr(user, "web_status", None)
    if raw_web is not None:
        user_info["web_status"] = str(raw_web)

    # ── Guild-member specific ────────────────────────────────────────────────
    user_info["server_name"] = guild.name if guild else "Direct Message"

    if isinstance(user, discord.Member):
        user_info["server_nickname"] = user.nick                               # May be None
        user_info["joined_server_at"] = (
            user.joined_at.strftime("%Y-%m-%d %H:%M UTC") if user.joined_at else None
        )
        user_info["server_roles"] = [r.name for r in user.roles if r.name != "@everyone"]
        user_info["top_role"]     = user.top_role.name if user.top_role else None
        user_info["server_booster_since"] = (
            user.premium_since.strftime("%Y-%m-%d") if user.premium_since else None
        )
        user_info["pending_membership_screening"] = user.pending
        timed_out = getattr(user, "timed_out_until", None)
        user_info["timed_out_until"] = timed_out.strftime("%Y-%m-%d %H:%M UTC") if timed_out else None
        # Guild avatar (separate from global avatar)
        guild_av = getattr(user, "guild_avatar", None)
        user_info["server_avatar_url"] = str(guild_av.url) if guild_av else None
        # Colour from top coloured role
        colour = user.colour
        if colour != discord.Colour.default():
            user_info["role_colour"] = str(colour)
        # Key guild permissions (non-exhaustive but informative)
        try:
            perms = user.guild_permissions
            user_info["guild_permissions"] = {
                "administrator":     perms.administrator,
                "manage_guild":      perms.manage_guild,
                "manage_channels":   perms.manage_channels,
                "manage_roles":      perms.manage_roles,
                "manage_messages":   perms.manage_messages,
                "kick_members":      perms.kick_members,
                "ban_members":       perms.ban_members,
                "moderate_members":  perms.moderate_members,
                "mention_everyone":  perms.mention_everyone,
            }
        except Exception:
            pass
    else:
        user_info["server_roles"]  = []
        user_info["top_role"]      = None

    # ── Activities / Rich Presence ───────────────────────────────────────────
    status_list: list[str] = []
    if hasattr(user, "activities"):
        for activity in user.activities:
            try:
                atype = activity.type
                name  = getattr(activity, "name", "Unknown")

                if atype == discord.ActivityType.listening:
                    # Spotify and generic listening
                    title   = getattr(activity, "title", None)   or name
                    artist  = getattr(activity, "artist", None)  or "Unknown Artist"
                    album   = getattr(activity, "album", None)
                    track_url = getattr(activity, "track_url", None)
                    entry = f"Listening to: {title} by {artist}"
                    if album:
                        entry += f" (Album: {album})"
                    if track_url:
                        entry += f" — {track_url}"
                    status_list.append(entry)

                elif atype == discord.ActivityType.playing:
                    details = getattr(activity, "details", None)
                    state   = getattr(activity, "state",   None)
                    ts    = getattr(activity, "timestamps", None)
                    start = getattr(ts, "start", None) if ts else None
                    entry = f"Playing: {name}"
                    if details:
                        entry += f" ({details}"
                        if state:
                            entry += f" — {state}"
                        entry += ")"
                    if start:
                        entry += f" [since {start.strftime('%H:%M UTC')}]"
                    status_list.append(entry)

                elif atype == discord.ActivityType.streaming:
                    platform = getattr(activity, "platform", "Unknown Platform")
                    url      = getattr(activity, "url", None)
                    entry = f"Streaming: {name} on {platform}"
                    if url:
                        entry += f" — {url}"
                    status_list.append(entry)

                elif atype == discord.ActivityType.watching:
                    status_list.append(f"Watching: {name}")

                elif atype == discord.ActivityType.competing:
                    status_list.append(f"Competing in: {name}")

                elif atype == discord.ActivityType.custom:
                    # Custom status has emoji + state text
                    emoji = getattr(activity, "emoji", None)
                    state = getattr(activity, "state", None)
                    parts = []
                    if emoji:
                        parts.append(str(emoji))
                    if state:
                        parts.append(state)
                    elif name and name != "Custom Status":
                        parts.append(name)
                    if parts:
                        status_list.append("Custom status: " + " ".join(parts))

            except Exception:
                continue

    user_info["activities"] = status_list

    # ── User Profile (requires an API call; may fail for non-friends/privacy) ──
    try:
        profile = await user.profile()
        user_info["bio"]           = getattr(profile, "bio", None)
        user_info["pronouns"]      = getattr(profile, "pronouns", None)

        prof_premium = getattr(profile, "premium_since", None)
        user_info["nitro_since"]   = prof_premium.strftime("%Y-%m-%d") if prof_premium else None

        # Nitro type (0=none, 1=classic, 2=full, 3=basic)
        nitro_type = getattr(profile, "premium_type", None)
        if nitro_type is not None:
            _nitro_labels = {0: "None", 1: "Nitro Classic", 2: "Nitro", 3: "Nitro Basic"}
            user_info["nitro_type"] = _nitro_labels.get(int(nitro_type), str(nitro_type))

        # Banner
        banner = getattr(profile, "banner", None) or getattr(user, "banner", None)
        user_info["banner_url"] = str(banner.url) if banner else None

        # Accent colour
        accent = getattr(profile, "accent_colour", None) or getattr(user, "accent_colour", None)
        user_info["accent_colour"] = str(accent) if accent else None

        # Connected accounts
        connected = getattr(profile, "connected_accounts", [])
        user_info["connections"] = [f"{c.type}: {c.name}" for c in connected] if connected else []

        # Mutual guilds / friends (available when fetching someone else's profile)
        mutual_guilds = getattr(profile, "mutual_guilds", None)
        if mutual_guilds is not None:
            user_info["mutual_guild_count"] = len(mutual_guilds)

        mutual_friends = getattr(profile, "mutual_friends", None)
        if mutual_friends is not None:
            user_info["mutual_friend_count"] = len(mutual_friends)

        # Recent activity & leaderboards
        recent = getattr(profile, "user_recent_activity", None)
        user_info["recent_activity"] = str(recent) if recent else None

        leaderboards = getattr(profile, "leaderboards", None)
        user_info["game_leaderboard"] = str(leaderboards) if leaderboards else None

    except Exception as e:
        logger.debug(f"Could not fetch profile for {user}: {e}")
        user_info.setdefault("bio",             None)
        user_info.setdefault("connections",     [])
        user_info.setdefault("recent_activity", None)

    return user_info


class GeminiSelfBot(discord.Client):
    def __init__(self, ollama_http_client: httpx.AsyncClient, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ollama_http_client = ollama_http_client
        self.start_time = int(time.time() * 1000)
        self.prompt_queue = PromptQueue(self)
        self.reminder_loop_started = False
        self.vc_join_time = None
        self.vc_connect_lock = asyncio.Lock()


    async def on_ready(self):
        # Set online status
        await self.change_presence(status=discord.Status.online)
        init_db()
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        
        # Check for pending restart notification
        restart_channel_id = get_system_state("pending_restart_channel")
        restart_message_id = get_system_state("pending_restart_message_id")
        
        if restart_channel_id:
            try:
                save_system_state("pending_restart_channel", None) 
                save_system_state("pending_restart_message_id", None)
                
                channel = self.get_channel(int(restart_channel_id)) or await self.fetch_channel(int(restart_channel_id))
                if channel:
                    success = False
                    if restart_message_id:
                        try:
                            msg = await channel.fetch_message(int(restart_message_id))
                            await msg.edit(content="> ✅ **Restarted and Online.**")
                            success = True
                        except:
                            pass # Fallback to sending new message
                    
                    if not success:
                        await channel.send("> ✅ **Restarted and Online.**")
            except Exception as e:
                logger.error(f"Failed to send/edit restart notification: {e}")

        logger.info("Self-bot is ready and listening for mentions/replies (Status: Online).")
        self.loop.create_task(status_loop(self))
        if not self.reminder_loop_started:
            self.loop.create_task(self.reminder_loop())
            self.reminder_loop_started = True
        self.prompt_queue.start()
        # stay_alive_loop removed as it is redundant with status_loop and can cause instability
        self.loop.create_task(self.vc_watchdog_loop())


        # Rejoin voice channel if persisted
        last_vc_id = get_system_state("last_vc_id")
        if last_vc_id:
            try:
                # We wait a bit to ensure internal caches are warm
                await asyncio.sleep(2)
                chan = self.get_channel(int(last_vc_id)) or await self.fetch_channel(int(last_vc_id))
                if chan and isinstance(chan, discord.VoiceChannel):
                    # Check if already in this voice channel to avoid "Already connected" error
                    existing_vc = discord.utils.get(self.voice_clients, guild=chan.guild)
                    if not existing_vc:
                        await chan.connect()
                        logger.info(f"Rejoined persisted VC: {chan.name} ({chan.id})")
                    else:
                        logger.info(f"Already connected to VC in guild {chan.guild.id}. Skipping rejoin.")
            except Exception as e:
                logger.error(f"Failed to rejoin persisted VC {last_vc_id}: {e}")

    async def on_connect(self):
        logger.info("Bot has connected to the Discord Gateway.")

    async def on_disconnect(self):
        logger.warning("Bot has disconnected from the Discord Gateway.")

    async def on_resumed(self):
        logger.info("Bot has successfully resumed the session.")

    async def on_voice_state_update(self, member, before, after):
        """
        Monitor voice state changes to log disconnections and attempt auto-reconnect if unexpected.
        Includes logic to rejoin original VC if moved by a moderator.
        """
        if member.id == self.user.id:
            last_vc_id = get_system_state("last_vc_id")
            
            # Case: Disconnected from a channel
            if before.channel and not after.channel:
                logger.info(f"Disconnected from voice channel: {before.channel.name} (ID: {before.channel.id})")
                
                # Check if this was an unexpected disconnect (i.e. last_vc_id is still set to this channel)
                if last_vc_id == str(before.channel.id):
                    logger.warning(f"Unexpectedly removed from VC {before.channel.id}. Attempting to reconnect in 10s...")
                    await asyncio.sleep(10)
                    
                    # Retry logic for reconnection
                    for attempt in range(3):
                        try:
                            # Re-fetch channel to ensure it still exists and we have access
                            chan = self.get_channel(before.channel.id) or await self.fetch_channel(before.channel.id)
                            if chan and isinstance(chan, discord.VoiceChannel):
                                # Double check we aren't already connected (e.g. library auto-reconnected)
                                async with self.vc_connect_lock:
                                    if not member.guild.voice_client:
                                        await chan.connect(timeout=15.0)
                                        logger.info(f"Successfully auto-reconnected to VC: {chan.name} (Attempt {attempt+1})")
                                        return
                                    else:
                                        logger.info(f"Already connected to VC: {chan.name}")
                                        return
                        except Exception as e:
                            logger.error(f"Auto-reconnect attempt {attempt+1} failed: {e}")
                            if attempt < 2:
                                await asyncio.sleep(5)
                    
                    logger.error("All auto-reconnect attempts failed. Watchdog will handle future retries.")
            
            # Case: Switched channels (could be a server move)
            elif before.channel and after.channel and before.channel.id != after.channel.id:
                if last_vc_id and str(after.channel.id) != last_vc_id:
                    logger.warning(f"Voice channel move detected (External): {before.channel.name} -> {after.channel.name}. Moving back to original VC...")
                    try:
                        orig_chan = self.get_channel(int(last_vc_id)) or await self.fetch_channel(int(last_vc_id))
                        if orig_chan and isinstance(orig_chan, discord.VoiceChannel):
                            # Short delay before moving back to avoid race conditions or being flagged
                            await asyncio.sleep(2)
                            await orig_chan.connect()
                            logger.info(f"Successfully moved back to original VC: {orig_chan.name}")
                        else:
                            raise Exception("Original channel no longer accessible or not a voice channel.")
                    except Exception as e:
                        logger.error(f"Failed to move back to original VC: {e}")
                        # If we can't move back, disconnect from the current channel and clear state
                        if member.guild.voice_client:
                            await member.guild.voice_client.disconnect()
                        save_system_state("last_vc_id", None)
                        save_system_state("vc_session_start", None)
                        logger.info("Disconnected from voice and cleared last_vc_id due to move-back failure.")
                else:
                    logger.info(f"Voice channel move detected: {before.channel.name} -> {after.channel.name}")
                    # Update state if it wasn't set yet (anchoring)
                    if not last_vc_id:
                        save_system_state("last_vc_id", str(after.channel.id))
            
            # Case: Joined a channel for the first time
            elif not before.channel and after.channel:
                logger.info(f"Joined voice channel: {after.channel.name} (ID: {after.channel.id})")
                if not last_vc_id:
                    save_system_state("last_vc_id", str(after.channel.id))
                    logger.info(f"Anchored last_vc_id to {after.channel.name} ({after.channel.id})")


    # stay_alive_loop was here (removed for stability)

    async def vc_watchdog_loop(self):
        """
        Checks every 5 minutes if the bot should be in a VC and reconnects if dropped.
        """
        await self.wait_until_ready()
        # Initial delay to let on_ready's rejoin logic finish first
        await asyncio.sleep(30)
        while not self.is_closed():
            try:
                last_vc_id = get_system_state("last_vc_id")
                if last_vc_id:
                    # Check if we are currently in any voice channel
                    if not self.voice_clients:
                        logger.warning(f"Voice Watchdog: Not in any VC but last_vc_id is {last_vc_id}. Reconnecting...")
                        try:
                            chan = self.get_channel(int(last_vc_id)) or await self.fetch_channel(int(last_vc_id))
                            if chan and isinstance(chan, discord.VoiceChannel):
                                async with self.vc_connect_lock:
                                    if not self.voice_clients:
                                        await chan.connect(timeout=15.0)
                                        logger.info(f"Voice Watchdog: Successfully reconnected to {chan.name}")
                                    else:
                                        logger.info(f"Voice Watchdog: Already reconnected to {chan.name} by another process.")
                            else:
                                logger.error(f"Voice Watchdog: Channel {last_vc_id} not found or not a VC. Clearing state.")
                                save_system_state("last_vc_id", None)
                        except Exception as e:
                            logger.error(f"Voice Watchdog: Reconnect failed with error: {e}. Will retry next cycle.")
            except Exception as e:
                logger.error(f"Error in vc_watchdog_loop: {e}")
                
            await asyncio.sleep(120) # 2 minutes (was 5m)


    async def vc_status_auto_loop(self):
        """
        Periodically updates the VC status if an autostatus template is set.
        """
        await self.wait_until_ready()
        last_reported_hour = -1
        
        while not self.is_closed():
            try:
                template = get_system_state("vc_autostatus_template")
                session_start_str = get_system_state("vc_session_start")
                
                if template and self.voice_clients:
                    # Use persistent start time if set, otherwise use bot's join time
                    start_time = float(session_start_str) if session_start_str else self.vc_join_time
                    
                    if start_time:
                        hours = int((time.time() - start_time) // 3600)
                        
                        if hours != last_reported_hour:
                            status_msg = template.replace("{hours}", str(hours))
                            
                            for vc in self.voice_clients:
                                try:
                                    route = discord.http.Route('PUT', '/channels/{channel_id}/voice-status', channel_id=vc.channel.id)
                                    await self.http.request(route, json={'status': status_msg})
                                except:
                                    pass
                            
                            last_reported_hour = hours
                            logger.info(f"Auto-updated VC status to: {status_msg}")
                else:
                    last_reported_hour = -1
            except Exception as e:
                logger.error(f"Error in vc_status_auto_loop: {e}")
                
            await asyncio.sleep(60) # Check every minute


    async def reminder_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                due = get_due_reminders()
                for r in due:
                    try:
                        channel = self.get_channel(r['channel_id']) or await self.fetch_channel(r['channel_id'])
                        if channel:
                            try:
                                msg = await channel.fetch_message(r['message_id'])
                                await msg.reply(f"> ⏰ **Reminder:** {r['topic']}")
                                delete_reminder(r['id'])
                            except discord.NotFound:
                                # Message was deleted - can't reply, so just send a normal message if possible
                                logger.warning(f"Reminder {r['id']}: Original message {r['message_id']} not found. Sending to channel instead.")
                                await channel.send(f"> ⏰ **Reminder:** {r['topic']}\n*(Note: The message I was supposed to reply to was deleted)*")
                                delete_reminder(r['id'])
                            except discord.Forbidden:
                                logger.error(f"Reminder {r['id']}: Forbidden from replying to message in channel {r['channel_id']}.")
                                # Depending on policy, we might want to delete it or leave it. 
                                # Let's delete it so it doesn't spam the log.
                                delete_reminder(r['id'])
                        else:
                            logger.error(f"Reminder {r['id']}: Could not find channel {r['channel_id']}.")
                            # If we can't find the channel at all, it's likely gone or we lost access.
                            # Delete it to avoid infinite looping.
                            delete_reminder(r['id'])
                    except Exception as e:
                        logger.error(f"Failed to process reminder {r['id']}: {e}")

            except Exception as e:
                logger.error(f"Error in reminder_loop: {e}")
            
            await asyncio.sleep(10)
            
    async def on_message(self, message: discord.Message):
        # 0. Admin Commands (Owner Only: tx24)
        if message.content.startswith(";gem"):
            is_owner = message.author.id == 504541573636161546
            is_self_admin = (message.author.id == self.user.id and message.guild and message.guild.id == 1490733173246660658)
            
            if is_owner or is_self_admin:
                parts = message.content.split()
                sub = parts[1].lower() if len(parts) > 1 else "help"
                
                # Helper for toggle commands
                def get_toggle_val(p):
                    return "true" if p.lower() in ("on", "true", "yes") else "false"
                
                if sub == "restart":
                    init_msg = await message.reply("> 🔄 **Initiating PM2 restart...**")
                    save_system_state("pending_restart_channel", str(message.channel.id))
                    save_system_state("pending_restart_message_id", str(init_msg.id))
                    subprocess.run(["pm2", "restart", "gemini-bot"])
                    return

                elif sub == "kill":
                    await message.reply("> 💀 **Terminating bot instance...**")
                    # This will trigger the finally block in main.py to clean up the lock file
                    await self.close()
                    return
                    
                elif sub == "model":
                    if len(parts) < 4:
                        await message.reply("> ❌ **Usage:** `;gem model <type> <model_id>` (types: text, image, video, song)")
                        return
                    m_type = parts[2].lower()
                    new_model = parts[3].strip()
                    
                    if m_type not in ("text", "image", "video", "song", "audio"):
                        await message.reply("> ❌ **Unknown type:** Must be text, image, video, or song.")
                        return

                    validate_msg = await message.reply(f"> 🔍 **Validating {m_type} model:** `{new_model}`...")
                    try:
                        # Use correct region for validation
                        v_loc = "global" if m_type == "text" else config.IMAGE_LOCATION if m_type == "image" else config.VIDEO_LOCATION if m_type == "video" else config.AUDIO_LOCATION
                        genai_client = get_client(location=v_loc if config.USE_VERTEX_AI else None)
                        await genai_client.aio.models.get(model=new_model)
                        
                        env_key = ""
                        if m_type == "text": env_key = "GEMINI_MODEL"
                        elif m_type == "image": env_key = "GEMINI_MODEL_IMAGE"
                        elif m_type == "video": env_key = "GEMINI_MODEL_VIDEO"
                        elif m_type in ("song", "audio"): env_key = "GEMINI_MODEL_AUDIO"
                        
                        config.update_config(env_key, new_model)
                        await validate_msg.edit(content=f"> ✅ **{m_type.capitalize()} model validated and changed to:** `{new_model}`")
                    except Exception as e:
                        await validate_msg.edit(content=f"> ❌ **Invalid Model:** `{new_model}` is not accessible or does not exist.\n> *Error: {str(e)[:300]}*")
                    return
                    
                elif sub == "config":
                    if len(parts) < 4:
                        if len(parts) == 3 and parts[2].lower() == "queue":
                            await message.reply(f"> ⏳ **Queue Status:** `{'Enabled' if config.ENABLE_QUEUE else 'Disabled'}`\n> **Usage:** `;gem config queue <on/off>`")
                            return
                        await message.reply("> ❌ **Usage:** `;gem config <key> <val>`\nKeys: `autothink`, `vertex`, `statusmsg`, `queue`, `budget`, `aspect`, `safety`, `people`, `image_res`, `video_res`, `fps`, `duration`, `audio_len`, `image_cost`, `video_cost`, `song_cost`")
                        return
                        
                    key = parts[2].lower()
                    val = parts[3]
                    is_on = get_toggle_val(val) == "true"
                    
                    if key == "autothink":
                        config.update_config("AUTO_THINKING", "true" if is_on else "false")
                        await message.reply(f"> ✅ **Auto-Thinking set to:** `{is_on}`")
                    elif key == "vertex":
                        config.update_config("USE_VERTEX_AI", "true" if is_on else "false")
                        await message.reply(f"> ✅ **Vertex AI set to:** `{is_on}`")
                    elif key == "statusmsg" or key == "loading":
                        config.update_config("SHOW_LOADING_MESSAGES", "true" if is_on else "false")
                        await message.reply(f"> ✅ **Status Messages set to:** `{is_on}`")
                    elif key == "queue":
                        config.update_config("ENABLE_QUEUE", "true" if is_on else "false")
                        await message.reply(f"> ✅ **Queue System set to:** `{is_on}`")
                    elif key == "budget":
                        try:
                            val_float = float(val)
                            config.update_config("DAILY_BUDGET", str(val_float))
                            await message.reply(f"> ✅ **Daily Budget set to:** `{val_float} €`")
                        except ValueError:
                            await message.reply("> ❌ **Invalid number for budget.**")
                    elif key in ("image_cost", "video_cost", "song_cost", "audio_cost"):
                        try:
                            val_float = float(val)
                            target_key = key.upper()
                            if target_key == "AUDIO_COST": target_key = "SONG_COST"
                            config.update_config(target_key, str(val_float))
                            await message.reply(f"> ✅ **{key.replace('_', ' ').capitalize()} set to:** `{val_float} €`")
                        except ValueError:
                            await message.reply(f"> ❌ **Invalid number for {key.replace('_', ' ')}.**")
                    elif key == "aspect":
                        config.update_config("ASPECT_RATIO", val)
                        await message.reply(f"> ✅ **Aspect Ratio set to:** `{val}`")
                    elif key == "safety":
                        config.update_config("SAFETY_FILTER_LEVEL", val)
                        await message.reply(f"> ✅ **Safety Filter set to:** `{val}`")
                    elif key == "people":
                        config.update_config("PERSON_GENERATION", val)
                        await message.reply(f"> ✅ **Person Generation set to:** `{val}`")
                    elif key == "fps":
                        config.update_config("VIDEO_FPS", val)
                        await message.reply(f"> ✅ **Video FPS set to:** `{val}`")
                    elif key == "duration":
                        config.update_config("VIDEO_DURATION", val)
                        await message.reply(f"> ✅ **Video Duration set to:** `{val}s`")
                    elif key == "video_res":
                        config.update_config("VIDEO_RES", val)
                        await message.reply(f"> ✅ **Video Resolution set to:** `{val}`")
                    elif key == "image_res":
                        config.update_config("IMAGE_RES", val)
                        await message.reply(f"> ✅ **Image Resolution set to:** `{val}`")
                    elif key == "audio_len":
                        config.update_config("AUDIO_DURATION", val)
                        await message.reply(f"> ✅ **Audio Max Length set to:** `{val}s`")
                    else:
                        await message.reply("> ❌ **Unknown config key.**")
                    return
                    
                elif sub == "whitelist":
                    if len(parts) < 3:
                        await message.reply("> ❌ **Usage:** `;gem whitelist <user_id>`")
                        return
                    try:
                        uid = int(parts[2].strip("<@!>"))
                        new_status = toggle_whitelist(uid)
                        await message.reply(f"> ✅ **User {uid} whitelist status:** `{'Enabled' if new_status else 'Disabled'}`")
                    except ValueError:
                        await message.reply("> ❌ **Invalid user ID.**")
                    return
                    
                elif sub == "budget":
                    spent = get_budget_spent()
                    remaining = max(0.0, config.DAILY_BUDGET - spent)
                    await message.reply(
                        f"> 💳 **Daily Budget Status**\n"
                        f"- **Limit:** `{config.DAILY_BUDGET} €`\n"
                        f"- **Spent:** `{spent:.4f} €`\n"
                        f"- **Remaining:** `{remaining:.4f} €`"
                    )
                    return

                elif sub == "vc":
                    if len(parts) < 3:
                        await message.reply("> ❌ **Usage:** `;gem vc <channel_id_or_tag>` or `;gem vc leave`")
                        return
                    
                    cmd = parts[2].lower()
                    if cmd == "leave":
                        if self.voice_clients:
                            save_system_state("last_vc_id", None)
                            save_system_state("vc_session_start", None)
                            count = len(self.voice_clients)
                            for vc in list(self.voice_clients):
                                await vc.disconnect()
                            await message.reply(f"> 👋 **Left {count} Voice Channel(s).**")
                        else:
                            await message.reply("> ❌ **Not currently in any voice channel.**")
                        return

                    elif cmd == "status":
                        if len(parts) < 4:
                            await message.reply("> ❌ **Usage:** `;gem vc status <message>`")
                            return
                        
                        status_msg = " ".join(parts[3:])
                        last_vc_id = get_system_state("last_vc_id")
                        target_vc = None
                        
                        if last_vc_id:
                            for vc in self.voice_clients:
                                if str(vc.channel.id) == last_vc_id:
                                    target_vc = vc
                                    break
                        
                        if not target_vc and self.voice_clients:
                            target_vc = self.voice_clients[0]
                            
                        if target_vc:
                            try:
                                # Update Voice Channel Status (Self-bot feature)
                                route = discord.http.Route('PUT', '/channels/{channel_id}/voice-status', channel_id=target_vc.channel.id)
                                await self.http.request(route, json={'status': status_msg})
                                await message.reply(f"> ✅ **VC Status set to:** `{status_msg}`")
                            except Exception as e:
                                await message.reply(f"> ❌ **Failed to set VC status:** `{str(e)}`")
                        else:
                            await message.reply("> ❌ **Not currently in a voice channel.**")
                        return

                    elif cmd == "autostatus":
                        if len(parts) < 3:
                            await message.reply("> ❌ **Usage:** `;gem vc autostatus <template> [--uptime HH:MM:SS]` or `;gem vc autostatus off`")
                            return
                        
                        full_val = " ".join(parts[3:]).strip()
                        if full_val.lower() in ("off", "none", "disable"):
                            save_system_state("vc_autostatus_template", None)
                            save_system_state("vc_session_start", None)
                            await message.reply("> ❌ **VC Auto-Status disabled.**")
                        else:
                            # Parse optional flags
                            template = full_val
                            manual_uptime_seconds = 0
                            
                            # 1. Handle --uptime HH:MM:SS
                            if "--uptime" in full_val:
                                t_parts = full_val.split("--uptime")
                                template = t_parts[0].strip()
                                uptime_str = t_parts[1].strip().split()[0]
                                try:
                                    up_parts = uptime_str.split(':')
                                    if len(up_parts) == 3: # HH:MM:SS
                                        manual_uptime_seconds = int(up_parts[0])*3600 + int(up_parts[1])*60 + int(up_parts[2])
                                    elif len(up_parts) == 2: # MM:SS
                                        manual_uptime_seconds = int(up_parts[0])*60 + int(up_parts[1])
                                    
                                    if manual_uptime_seconds > 0:
                                        session_start = time.time() - manual_uptime_seconds
                                        save_system_state("vc_session_start", str(session_start))
                                except:
                                    await message.reply("> ⚠️ **Invalid uptime format.** Use `HH:MM:SS`. Ignoring flag.")
                            
                            # 2. Handle --start <timestamp>
                            elif "--start" in full_val:
                                t_parts = full_val.split("--start")
                                template = t_parts[0].strip()
                                start_str = t_parts[1].strip().split()[0]
                                try:
                                    start_ts = float(start_str)
                                    # Detect milliseconds (timestamps > year 3000 in seconds are likely ms)
                                    if start_ts > 32503680000: 
                                        start_ts /= 1000
                                    save_system_state("vc_session_start", str(start_ts))
                                    manual_uptime_seconds = -1 # Flag that we used a timestamp
                                except:
                                    await message.reply("> ⚠️ **Invalid start timestamp.** Ignoring flag.")
                            
                            if "{hours}" not in template:
                                await message.reply("> ⚠️ **Warning:** Your template doesn't contain `{hours}`. It will be a static message.")
                            
                            save_system_state("vc_autostatus_template", template)
                            if not self.vc_join_time and self.voice_clients:
                                self.vc_join_time = time.time()
                                
                            reply_msg = f"> ✅ **VC Auto-Status set to:** `{template}`"
                            if manual_uptime_seconds > 0:
                                reply_msg += f"\n> 🕒 **Synced with current uptime:** `{uptime_str}`"
                            elif manual_uptime_seconds == -1:
                                reply_msg += f"\n> 🕒 **Synced with start timestamp:** `{start_str}`"
                            await message.reply(reply_msg)
                        return

                    chan_str = cmd.strip("<#>")
                    try:
                        chan_id = int(chan_str)
                        vc_chan = self.get_channel(chan_id) or await self.fetch_channel(chan_id)
                        if vc_chan and isinstance(vc_chan, discord.VoiceChannel):
                            # Move behavior: disconnect from existing VCs first
                            if self.voice_clients:
                                for vc in list(self.voice_clients):
                                    # Only disconnect if it's a different guild; connect() handles same-guild moves better
                                    if vc.guild.id != vc_chan.guild.id:
                                        await vc.disconnect()
                            
                            save_system_state("last_vc_id", str(chan_id))
                            await vc_chan.connect()
                            await message.reply(f"> ✅ **Joined VC:** `{vc_chan.name}`")
                        else:
                            await message.reply("> ❌ **Channel not found or not a voice channel.**")
                    except Exception as e:
                        await message.reply(f"> ❌ **Error:** {str(e)}")
                    return

                elif sub == "pause":
                    config.update_config("IS_PAUSED", "true")
                    await message.reply("> ⏸️ **Bot Paused.**")
                    return
                    
                elif sub == "resume":
                    config.update_config("IS_PAUSED", "false")
                    await message.reply("> ▶️ **Bot Resumed.**")
                    return

                elif sub == "join":
                    if len(parts) < 3:
                        await message.reply("> ❌ **Usage:** `;gem join <invite_link_or_code>`")
                        return
                    invite_input = parts[2].strip()
                    try:
                        # discord.py-self method to accept invites
                        invite = await self.accept_invite(invite_input)
                        await message.reply(f"> ✅ **Joined Server:** `{invite.guild.name}` ({invite.guild.id})")
                    except Exception as e:
                        logger.error(f"Failed to join server: {e}")
                        await message.reply(f"> ❌ **Failed to join:** `{str(e)}`")
                    return

                elif sub == "help":
                    # Full admin help for the owner
                    help_text = (
                        "> 🛠️ **Admin Commands**\n"
                        "- `;gem config <key> <val>`: Set config (autothink, vertex, statusmsg, queue, budget, aspect, safety, people, image_res, video_res, fps, duration, audio_len, image_cost, video_cost, song_cost).\n"
                        "- `;gem model <type> <id>`: Switch model (text, image, video, song).\n"
                        "- `;gem whitelist <id>`: Toggle user access to media commands.\n"
                        "- `;gem budget`: Show daily spending and limits.\n"
                        "- `;gem vc <id>`: Join a voice channel.\n"
                        "- `;gem vc leave`: Leave all voice channels.\n"
                        "- `;gem vc status <text>`: Set current VC status message.\n"
                        "- `;gem pause/resume`: Control bot processing.\n"
                        "- `;gem join <invite>`: Join a Discord server.\n"
                        "- `;gem restart`: Force PM2 process restart.\n\n"
                        "> 🎨 **Media Commands** (Privileged)\n"
                        "- `;gem image <prompt> [--stats]`: Generate an image.\n"
                        "- `;gem video <prompt> [--stats]`: Generate a video.\n"
                        "- `;gem song <prompt> [--stats]`: Generate a song/audio.\n\n"
                        "> 🎭 **Persona Commands**\n"
                        "- `;gem prompt <name> [user]`: Switch user personality.\n"
                        "- `;gem prompts`: List all variations with descriptions.\n"
                        "- `;gem status`: Show current personality and system status.\n"
                        "- `;gem help`: Show this list."
                    )
                    await message.reply(help_text)
                    return

        # 1. Ignore messages from yourself (to prevent infinite loops)
        # Exception: Allow self-messages in server 1490733173246660658 if they start with ;gem
        if message.author.id == self.user.id:
            if message.guild and message.guild.id == 1490733173246660658 and message.content.startswith(";gem"):
                logger.info("Self-message detected in target server with prefix ;gem - processing.")
            else:
                return
            
        # 2. Ignore messages from other bots
        if message.author.bot:
            return

        # 3. Check for triggers: Direct Mention or Reply to self
        is_mentioned = False
        
        # Exception: Self-messages starting with ;gem count as mentioned in target server
        if message.author.id == self.user.id and message.guild and message.guild.id == 1490733173246660658 and message.content.startswith(";gem"):
            is_mentioned = True

        if any(mention.id == self.user.id for mention in message.mentions):
            is_mentioned = True
            
        # Also trigger if they explicitly type @gemini
        if "@gemini" in message.content.lower():
            is_mentioned = True
            
        is_reply_to_self = False
        reply_content = None
        other_users_info = []
        relevant_users_scanned = set()
        
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
                        other_users_info.append(await extract_user_metadata(ref_msg.author, message.guild))
                        relevant_users_scanned.add(ref_msg.author.id)
            except discord.HTTPException:
                pass

        # 0.5 Public Persona Commands
        if message.content.startswith(";gem"):
            parts = message.content.split()
            sub = parts[1].lower() if len(parts) > 1 else "help"
            
            if sub in ("image", "video", "song"):
                is_owner = message.author.id == 504541573636161546
                is_self_admin = (message.author.id == self.user.id and message.guild and message.guild.id == 1490733173246660658)
                whitelisted = is_whitelisted(message.author.id)
                
                if not (is_owner or is_self_admin or whitelisted):
                    await message.reply("> ❌ **Unauthorized:** You are not on the whitelist to use media generation commands.")
                    return
                
                # Budget Check (Owner Exception)
                spent = get_budget_spent()
                if spent >= config.DAILY_BUDGET and not (is_owner or is_self_admin):
                    await message.reply(f"> ❌ **Budget Exhausted:** The daily media generation budget ({config.DAILY_BUDGET} €) has been reached. Only the owner can use these commands now.")
                    return

                show_gen_stats = "--stats" in message.content.lower()
                prompt = " ".join(parts[2:]).replace("--stats", "").strip()
                
                if not prompt:
                    await message.reply(f"> ❌ **Usage:** `;gem {sub} <prompt> [--stats]`")
                    return
                    
                model_name = ""
                cost = 0.0
                if sub == "image":
                    model_name = config.GEMINI_MODEL_IMAGE
                    cost = config.IMAGE_COST
                elif sub == "video":
                    model_name = config.GEMINI_MODEL_VIDEO
                    cost = config.VIDEO_COST
                elif sub == "song":
                    model_name = config.GEMINI_MODEL_AUDIO
                    cost = config.SONG_COST
                    
                loading_msg = await message.reply(f"> ⏳ ***Generating {sub} using {model_name}...***")
                try:
                    # For media generation in Vertex AI, we use the specific region (Lyria needs us-east5)
                    loc = config.IMAGE_LOCATION if sub == "image" else config.VIDEO_LOCATION if sub == "video" else config.AUDIO_LOCATION
                    genai_client = get_client(location=loc if config.USE_VERTEX_AI else None)
                    media_bytes = None
                    filename = ""
                    
                    start_gen = time.time()
                    if sub == "image":
                        if "gemini" in model_name.lower():
                            # Gemini multimodal image generation
                            # Map 1080p to 1K for imageSize
                            size_map = {"1080p": "1K", "4k": "4K", "2k": "2K", "720p": "0.5K"}
                            mapped_size = size_map.get(config.IMAGE_RES.lower(), "1K")
                            
                            res = await genai_client.aio.models.generate_content(
                                model=model_name,
                                contents=prompt,
                                config=types.GenerateContentConfig(
                                    response_modalities=["IMAGE"],
                                    image_config=types.ImageConfig(
                                        aspect_ratio=config.ASPECT_RATIO,
                                        image_size=mapped_size
                                    )
                                )
                            )
                            if res.candidates and res.candidates[0].content.parts:
                                for part in res.candidates[0].content.parts:
                                    if part.inline_data and part.inline_data.data:
                                        media_bytes = part.inline_data.data
                                        break
                        else:
                            # Standard Imagen generation
                            res = await genai_client.aio.models.generate_images(
                                model=model_name, 
                                prompt=prompt,
                                config=types.GenerateImagesConfig(
                                    aspect_ratio=config.ASPECT_RATIO,
                                    safety_filter_level=config.SAFETY_FILTER_LEVEL,
                                    person_generation=config.PERSON_GENERATION
                                )
                            )
                            if res.generated_images:
                                media_bytes = res.generated_images[0].image.image_bytes
                        
                        if media_bytes:
                            filename = f"generated_image_{int(time.time())}.png"
                    elif sub == "video":
                        res = await genai_client.aio.models.generate_videos(
                            model=model_name, 
                            prompt=prompt,
                            config=types.GenerateVideosConfig(
                                fps=config.VIDEO_FPS,
                                duration_seconds=config.VIDEO_DURATION,
                                aspect_ratio=config.ASPECT_RATIO,
                                resolution=config.VIDEO_RES
                            )
                        )
                        if res.generated_videos:
                            media_bytes = res.generated_videos[0].video.video_bytes
                            filename = f"generated_video_{int(time.time())}.mp4"
                    elif sub == "song":
                        # Lyria models often have specific requirements or fixed lengths
                        is_clip = "clip" in model_name.lower()
                        duration = 30 if is_clip else config.AUDIO_DURATION
                        audio_prompt = f"{prompt} (Length: {duration}s)"
                        
                        res = await genai_client.aio.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_modalities=['AUDIO'],
                                candidate_count=1
                            )
                        )
                        for part in res.candidates[0].content.parts:
                            if part.inline_data and part.inline_data.data:
                                media_bytes = part.inline_data.data
                                ext = "wav"
                                if part.inline_data.mime_type and "mp3" in part.inline_data.mime_type:
                                    ext = "mp3"
                                filename = f"generated_audio_{int(time.time())}.{ext}"
                                break
                    gen_time = time.time() - start_gen

                    if media_bytes:
                        add_to_budget_spent(cost)
                        new_spent = get_budget_spent()
                        remaining = max(0.0, config.DAILY_BUDGET - new_spent)
                        
                        footer = f"> 💳 **Cost:** `{cost} €` | **Remaining:** `{remaining:.4f} €`"
                        if show_gen_stats:
                            footer += f"\n> ⏱️ **Time:** `{gen_time:.2f}s` | **Model:** `{model_name}`"
                            
                        file_attachment = discord.File(io.BytesIO(media_bytes), filename=filename)
                        await message.reply(content=footer, file=file_attachment)
                        await loading_msg.delete()
                    else:
                        await loading_msg.edit(content=f"> ❌ **Failed:** No {sub} data returned by the model.")
                except Exception as e:
                    logger.error(f"Media generation error: {e}")
                    err_code = extract_error_code(e)
                    status_str, friendly_msg = LLM_ERROR_MAPPING.get(err_code, ("Unknown Error", f"Oops! I encountered an unexpected error while generating {sub}."))
                    
                    await loading_msg.edit(content=f"> ❌ **{status_str}:** {friendly_msg}\n> *Error code: {err_code}*")
                return

            elif sub == "prompts" or (sub == "prompt" and len(parts) < 3):
                # Context-aware active variation
                user_var = get_user_settings(message.author.id).get('variation', 'default')
                chan_var = get_channel_settings(message.channel.id).get('variation', 'default') if message.channel else 'default'
                guild_var = get_server_settings(message.guild.id).get('variation', 'default') if message.guild else 'default'
                
                active_var = 'default'
                if user_var != 'default': active_var = user_var
                elif chan_var != 'default': active_var = chan_var
                elif guild_var != 'default': active_var = guild_var
                
                desc_lines = []
                for k, v in config.PROMPT_DESCRIPTIONS.items():
                    markers = []
                    if k == user_var: markers.append("👤")
                    if k == chan_var and chan_var != 'default': markers.append("💬")
                    if k == guild_var and guild_var != 'default': markers.append("🌐")
                    
                    marker_str = f" {' '.join(markers)}" if markers else ""
                    if k == active_var: marker_str += " 🟢"
                    
                    desc_lines.append(f"- **{k.capitalize()}**: {v}{marker_str}")
                
                final_msg = "> 📜 **Available Prompt Variations**\n"
                final_msg += f"> Legend: 👤 User | 💬 Channel | 🌐 Server | 🟢 Active\n\n"
                final_msg += "\n".join(desc_lines) + "\n\n> **Usage:** `;gem prompt [channel|server] <name>`"
                
                if len(final_msg) > 1950:
                    parts_msg = [final_msg[i:i+1900] for i in range(0, len(final_msg), 1900)]
                    for p in parts_msg: await message.reply(p)
                else: await message.reply(final_msg)
                return

            elif sub == "prompt":
                if len(parts) < 3:
                    await message.reply("> ❌ **Missing variation name.** Use `;gem prompts` to see the list.")
                    return
                
                level = "user"
                variation_name = parts[2].lower()
                
                if variation_name == "channel":
                    level = "channel"
                    if len(parts) < 4:
                        await message.reply("> ❌ **Usage:** `;gem prompt channel <name>`")
                        return
                    variation_name = parts[3].lower()
                elif variation_name == "server" or variation_name == "guild":
                    level = "server"
                    if len(parts) < 4:
                        await message.reply("> ❌ **Usage:** `;gem prompt server <name>`")
                        return
                    variation_name = parts[3].lower()

                if variation_name in config.PROMPT_MODIFIERS:
                    is_owner = message.author.id == 504541573636161546
                    is_self_admin = (message.author.id == self.user.id and message.guild and message.guild.id == 1490733173246660658)
                    is_admin = False
                    if message.guild:
                        perms = message.author.guild_permissions
                        if level == "channel":
                            is_admin = perms.manage_channels or perms.administrator
                        elif level == "server":
                            is_admin = perms.manage_guild or perms.administrator
                    
                    can_manage = is_owner or is_self_admin or is_admin

                    if level == "user":
                        target_user = message.author
                        if len(parts) > 3 and is_owner:
                            try:
                                # Mentions format is <@id> or <@!id>
                                user_str = parts[3].strip('<@!>')
                                if not user_str.isdigit():
                                    raise ValueError()
                                target_user_id = int(user_str)
                                target_user = self.get_user(target_user_id) or await self.fetch_user(target_user_id)
                            except Exception:
                                await message.reply("> ❌ **Invalid user ID or mention.**")
                                return
                                
                        save_user_variation(target_user.id, variation_name)
                        if target_user.id == message.author.id:
                            await message.reply(f"> ✅ **User personality shifted to:** `{variation_name.capitalize()}`")
                        else:
                            await message.reply(f"> ✅ **Personality for {target_user.name} shifted to:** `{variation_name.capitalize()}`")
                    
                    elif level == "channel":
                        if not can_manage:
                            await message.reply("> ❌ **Permission Denied:** You need `Manage Channels` permission.")
                            return
                        save_channel_variation(message.channel.id, variation_name)
                        await message.reply(f"> ✅ **Channel personality shifted to:** `{variation_name.capitalize()}`")
                        
                    elif level == "server":
                        if not can_manage:
                            await message.reply("> ❌ **Permission Denied:** You need `Manage Server` permission.")
                            return
                        save_server_variation(message.guild.id, variation_name)
                        await message.reply(f"> ✅ **Server personality shifted to:** `{variation_name.capitalize()}`")
                else:
                    await message.reply(f"> ❌ **Unknown variation:** `{variation_name}`. Use `;gem prompts` to see the list.")
                return

            elif sub == "help" and message.author.id != 504541573636161546:
                # Public restricted help
                is_whitelisted_user = is_whitelisted(message.author.id)
                
                help_text = "> 🎭 **Gemini Persona Commands**\n"
                help_text += "- `;gem prompt <name>`: Switch style for yourself.\n"
                help_text += "- `;gem prompt channel <name>`: Switch style for this channel.\n"
                help_text += "- `;gem prompt server <name>`: Switch style for this server.\n"
                help_text += "- `;gem prompts`: List all available personalities.\n"
                help_text += "- `;gem status`: Show current personality and system status.\n"
                
                if is_whitelisted_user:
                    help_text += (
                        "\n> 🎨 **Media Commands** (Whitelisted)\n"
                        "- `;gem image <prompt> [--stats]`: Generate an image.\n"
                        "- `;gem video <prompt> [--stats]`: Generate a video.\n"
                        "- `;gem song <prompt> [--stats]`: Generate a song/audio.\n"
                    )
                
                help_text += "- `;gem help`: Show this message."
                await message.reply(help_text)
                return

            elif sub == "status":
                mode = "Vertex AI" if config.USE_VERTEX_AI else "AI Studio"
                paused_status = "⏸️ PAUSED" if config.IS_PAUSED else "▶️ RUNNING"
                think_status = "🧠 ON" if config.AUTO_THINKING else "⚪ OFF"
                queue_status = "⏳ ON" if config.ENABLE_QUEUE else "⚪ OFF"
                
                # Fetch settings for all levels
                user_var = get_user_settings(message.author.id).get('variation', 'default')
                chan_var = get_channel_settings(message.channel.id).get('variation', 'default') if message.channel else 'default'
                guild_var = get_server_settings(message.guild.id).get('variation', 'default') if message.guild else 'default'
                
                active_var = 'default'
                if user_var != 'default': active_var = user_var
                elif chan_var != 'default': active_var = chan_var
                elif guild_var != 'default': active_var = guild_var
                
                await message.reply(
                    f"> 📊 **System Status**\n"
                    f"- **Model:** `{config.GEMINI_MODEL}`\n"
                    f"- **Active Style:** `{active_var.capitalize()}` 🟢\n"
                    f"- **User Style:** `{user_var.capitalize()}`\n"
                    f"- **Channel Style:** `{chan_var.capitalize()}`\n"
                    f"- **Server Style:** `{guild_var.capitalize()}`\n"
                    f"- **Mode:** `{mode}`\n"
                    f"- **Thinking:** `{think_status}`\n"
                    f"- **Queue:** `{queue_status}`\n"
                    f"- **State:** `{paused_status}`"
                )
                return

        if not (is_mentioned or is_reply_to_self):
            return
            


        # 4. Check for Pause State (Owner Exception)
        if config.IS_PAUSED and message.author.id != 504541573636161546:
            return # Silent ignore when paused

        # 5. Process the message
        logger.info(f"Triggered by {message.author}: '{message.content}'")
        
        # Clean the input
        user_prompt = clean_mention(message.content, self.user.id)
        
        # Handle file attachments (before we decide whether to proceed)
        # Core Safety Guardrail
        # Deny obvious malicious patterns immediately to save compute
        is_safe, refusal_reason = is_safe_prompt(user_prompt)
        if not is_safe:
            logger.warning(f"Guardrail blocked request from {message.author}: {refusal_reason}")
            await message.reply(f"> 🛡️ **Guardrail Triggered:** {refusal_reason}\nI cannot fulfill this request.")
            return

        # Initialize context containers
        attachments_text = None
        loading_msg = None

        # 1. Identify all attachments (Current Message)
        all_attachments = list(message.attachments)
        
        # 2. Identify all attachments (Replied Message)
        ref_msg = None
        if message.reference:
            try:
                ref_msg = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
                if ref_msg and ref_msg.attachments:
                    all_attachments.extend(ref_msg.attachments)
            except discord.HTTPException:
                pass

        # 3. Handle All Attachments
        attachments_text = ""
        media_data = []
        if all_attachments:
            results, att_err = await read_attachments(all_attachments)
            if att_err:
                await message.reply(f"> {att_err}")
                return
            
            for item in results:
                # If it's small and potentially text-based, embed it directly in the prompt
                is_embedded = False
                if item["size"] < config.MAX_TEXT_EMBED_BYTES:
                    try:
                        text_content = item["data"].decode("utf-8")
                        attachments_text += f"### Attached file: `{item['filename']}`\n```\n{text_content}\n```\n\n"
                        is_embedded = True
                        logger.info(f"Embedded text file: {item['filename']}")
                    except UnicodeDecodeError:
                        pass # Not text, treat as media part
                
                # Always pass images, videos, and large/binary files as media parts
                if not is_embedded or item["mime_type"].startswith(("image/", "video/", "audio/")):
                    media_data.append(item)
                    logger.info(f"Prepared media part: {item['filename']} ({item['mime_type']})")

        # 5. Early exit if no content to process
        is_reply = message.reference is not None
        if not user_prompt and not is_reply and not attachments_text and not media_data:
            return

        # 6. Display final loading state if not already set by image processing
        status_task = None
        if loading_msg is None and config.SHOW_LOADING_MESSAGES:
            loading_msg = await message.reply(f"> ⏳ ***{random.choice(PHRASES_PARSING)}***")
            status_task = asyncio.create_task(rotate_status(loading_msg, PHRASES_PARSING, original_msg=message))
        elif loading_msg and config.SHOW_LOADING_MESSAGES:
            # If image analysis was already running, start rotating parsing phrases
            status_task = asyncio.create_task(rotate_status(loading_msg, PHRASES_PARSING, original_msg=message))
        elif not config.SHOW_LOADING_MESSAGES:
            status_task = asyncio.create_task(rotate_status(None, [], original_msg=message))

        status_data = {"task": status_task}





        # 5. Fetch Channel History (Context awareness)
        history = []
        recent_users_map = {}
        async for msg in message.channel.history(limit=config.CHANNEL_HISTORY_LIMIT):
             if msg.id == message.id: continue # Skip the current trigger message
             history.append({"author": str(msg.author), "content": msg.content})
             if msg.author.id not in recent_users_map and msg.author.id != self.user.id and msg.author.id != message.author.id:
                 recent_users_map[msg.author.id] = msg.author
                 
        # Reverse history so it's chronologically ordered (Oldest -> Newest) for the LLM
        history.reverse()

        # Extract extra context if users mention others or refer to them by name
        if len(other_users_info) < 3:
            # 1. Direct Mentions
            for m in message.mentions:
                if m.id != self.user.id and m.id != message.author.id and m.id not in relevant_users_scanned:
                    try:
                        other_users_info.append(await extract_user_metadata(m, message.guild))
                        relevant_users_scanned.add(m.id)
                    except: pass
                    if len(other_users_info) >= 3: break
            
            # 2. Aggressive Name Matching (Recent Users & Guild Members)
            prompt_lower = user_prompt.lower()
            if len(prompt_lower) > 2 and len(other_users_info) < 3:
                # Combine recent users and guild members for a wider search
                search_pool = list(recent_users_map.values())
                if message.guild:
                    # Only add a few extra guild members to avoid massive loops, 
                    # prioritizing those with distinctive names mentioned
                    search_pool.extend([m for m in message.guild.members if m.id not in recent_users_map])
                
                for u in search_pool:
                    if u.id == self.user.id or u.id == message.author.id or u.id in relevant_users_scanned:
                        continue
                        
                    # Safely handle attributes that might be None
                    names = [
                        (u.name or "").lower(), 
                        (getattr(u, 'display_name', '') or "").lower(), 
                        (getattr(u, 'global_name', '') or "").lower()
                    ]
                    # Check for whole word match to avoid false positives (e.g. "hi" matching "Hillary")
                    matched = False
                    for n in names:
                        if n and len(n) > 2:
                            # Use regex for word boundary matching
                            if re.search(rf'\b{re.escape(n)}\b', prompt_lower):
                                matched = True
                                break
                    
                    if matched:
                        other_users_info.append(await extract_user_metadata(u, message.guild))
                        relevant_users_scanned.add(u.id)
                        if len(other_users_info) >= 3:
                            break

        # 6. Extract User Metadata (Identity awareness)
        user_info = await extract_user_metadata(message.author, message.guild)

        # 7. Add to queue (or process immediately if disabled)
        if config.ENABLE_QUEUE:
            success, position = await self.prompt_queue.put(
                message.author.id,
                message,
                loading_msg,
                user_prompt,
                reply_content,
                is_reply_to_self,
                history=history,
                user_info=user_info,
                other_users_info=other_users_info,
                attachments_text=attachments_text,
                media_data=media_data,
                status_data=status_data,
            )

            if not success:
                err_text = "> ❌ **Queue Full:** You already have an active prompt being processed or in the queue."
                if loading_msg:
                    await loading_msg.edit(content=err_text)
                else:
                    await message.reply(err_text)
                return

            if position > 0 and loading_msg:
                await safe_cancel_status(status_data["task"])
                await loading_msg.edit(content=f"> ⏳ ***Queued (Position #{position})...***")
                # Update the task in the container so the worker picks up the QUEUE rotation
                status_data["task"] = asyncio.create_task(rotate_status(loading_msg, PHRASES_QUEUE, prefix=f"> ⏳ ***Queued (Pos #{position}) | ", original_msg=message))
                
                logger.info(f"on_message: User {message.author.id} queued at pos {position}")
            elif position > 0 and not config.SHOW_LOADING_MESSAGES:
                await safe_cancel_status(status_data["task"])
                status_data["task"] = asyncio.create_task(rotate_status(None, [], original_msg=message))
                
                logger.info(f"on_message: User {message.author.id} queued at pos {position} (Silent Mode)")
            else:
                logger.info(f"on_message: User {message.author.id} added at pos 0 (Immediate Processing)")
        else:
            # Queue is disabled - process immediately
            logger.info(f"on_message: Queue disabled. Processing User {message.author.id} immediately.")
            await self.process_queued_prompt(
                message,
                loading_msg,
                user_prompt,
                reply_content,
                is_reply_to_self,
                history,
                user_info,
                other_users_info=other_users_info,
                attachments_text=attachments_text,
                media_data=media_data,
                status_data=status_data
            )

    async def process_queued_prompt(self, message: discord.Message, loading_msg: discord.Message, user_prompt: str, reply_content: str, is_reply_to_self: bool, history: list, user_info: dict, other_users_info: list | None = None, attachments_text: str | None = None, media_data: list[dict] | None = None, status_data: dict | None = None):
        """
        Agentic loop allowing the main model to decide if it needs tools, thinking, or direct answer.
        """
        logger.info(f"process_queued_prompt: Starting agentic loop for user {message.author.id}")
        
        status_task = status_data.get("task") if status_data else None
        
        try:
            # 1. Context Compression (Disabled for large context)
            # Bypassing slow summarization LLM calls entirely to leverage Ollama's native KV Prompt Cache for instant evaluation.
            recap = None
            short_history = history

            # 2. Extract internal bot flags and clean prompt BEFORE building context
            show_stats = "--stats" in user_prompt.lower()
            force_search_flag = "--search" in user_prompt.lower()
            
            for flag in ["--stats", "--search"]:
                user_prompt = re.sub(rf"(?i){re.escape(flag)}\b", "", user_prompt)
            user_prompt = " ".join(user_prompt.split()).strip()

            # 3. Build final prompt structures
            user_info_for_context = user_info if not getattr(self, "ANONYMOUS_PROMPT", False) else None
            other_for_context = other_users_info if not getattr(self, "ANONYMOUS_PROMPT", False) else None
            # Fetch current context variation (Priority: Reply Chain > User > Channel > Server)
            user_var = get_user_settings(message.author.id).get('variation', 'default')
            chan_var = get_channel_settings(message.channel.id).get('variation', 'default') if message.channel else 'default'
            guild_var = get_server_settings(message.guild.id).get('variation', 'default') if message.guild else 'default'
            
            variation = 'default'
            if user_var != 'default':
                variation = user_var
            elif chan_var != 'default':
                variation = chan_var
            elif guild_var != 'default':
                variation = guild_var
            
            # Override with reply chain context if replying to the bot (Highest priority for consistency)
            if is_reply_to_self and message.reference and message.reference.message_id:
                chained_variation = get_message_variation(message.reference.message_id)
                if chained_variation:
                    variation = chained_variation
            
        
            messages = build_context(user_prompt, reply_content, is_reply_to_self, history=short_history, recap=recap, user_info=user_info_for_context, other_users_info=other_for_context, bot_username=str(self.user), media_data=media_data, variation=variation)

            # Inject attachment content as a system message so the model can reason over the files
            if attachments_text:
                messages.insert(1, {
                    "role": "system",
                    "content": (
                        "[Attached Files]:\n"
                        "The user has shared the following file content. "
                        "Refer to it when answering their question.\n\n"
                        + attachments_text
                    )
                })
            
            # 4. Fetch Persistent Memories (Author and Other Relevant Users)
            mems_to_fetch = [(message.author.id, "You")]
            if other_users_info:
                for extra in other_users_info:
                    mems_to_fetch.append((extra.get('id'), extra.get('display_name') or extra.get('username')))
            
            # We process manually to ensure clear labeling
            for u_id, name in mems_to_fetch:
                if not u_id: continue
                user_mems = get_memories(u_id)
                if user_mems:
                    brain = "\n".join([f"- {m['key']}: {m['value']}" for m in user_mems])
                    label = f"[User Facts & Memory - {name} (ID: {u_id})]:"
                    messages.insert(1, {"role": "system", "content": f"{label}\n{brain}"})

            # 5. Fetch Global Keyword Memories
            all_keywords = get_keyword_memories()
            if all_keywords:
                matched_keywords = []
                prompt_low = user_prompt.lower()
                for kw_obj in all_keywords:
                    kw = kw_obj['keyword']
                    if re.search(rf'\b{re.escape(kw)}\b', prompt_low):
                        matched_keywords.append(f"- {kw.capitalize()}: {kw_obj['value']}")
                
                if matched_keywords:
                    kw_block = "\n".join(matched_keywords)
                    messages.insert(1, {"role": "system", "content": f"[Keyword Facts & Information]:\n{kw_block}"})
            
            curr_phrases = PHRASES_DEFAULT
            # --- AGENTIC REACT LOOP ---
            iteration = 0
            max_iterations = 8
            executed_tools = set() # Track to avoid infinite loops
            
            # Tracking session stats
            session_tokens = 0
            last_tps = 0.0
            accumulated_thought = ""
            think_enabled = config.AUTO_THINKING  # starts True if native thinking is on
            
            while iteration < max_iterations:
                iteration += 1
                
                # Start/Restart rotation if needed
                await safe_cancel_status(status_task)
                status_task = asyncio.create_task(rotate_status(loading_msg, curr_phrases, original_msg=message))
                if status_data: status_data["task"] = status_task
                
                try:
                    # 1. Call LLM
                    llm_res = await ask_llm(messages, client=self.ollama_http_client)
                    response = llm_res.get("content", "")
                    session_tokens += llm_res.get("tokens", 0)
                    last_tps = llm_res.get("tps", 0.0)
                    
                    # Capture native reasoning parts
                    if llm_res.get("thought"):
                        accumulated_thought += llm_res["thought"] + "\n"
                    
                    if not response or response.strip() == "":
                        logger.error(f"Error in agent loop iteration {iteration}: Empty response.")
                        if think_enabled:
                            response = "I have finished my reasoning process. How can I help you further?"
                        else:
                            break

                    # Detect LLM error strings and format them
                    if response.startswith("🚨 [LLM_ERROR]"):
                        error_code = llm_res.get("error_code")
                        status_str, friendly_msg = LLM_ERROR_MAPPING.get(error_code, ("Unknown Error", "Oops! I encountered an unexpected error."))
                        
                        response = f"{friendly_msg}\n-# Error code: {error_code}, {status_str}"
                        
                        # Log and break to send this error as the final response
                        logger.warning(f"LLM returned error on iteration {iteration}: {response[:100]}")
                        
                        # We exit the iteration loop and send the response
                        await self._send_safe_response(
                            loading_msg, 
                            response, 
                            message, 
                            tokens=session_tokens, 
                            tps=last_tps, 
                            show_stats=show_stats, 
                            used_model=llm_res.get("model", ""),
                            variation=variation
                        )
                        increment_stats(tokens=session_tokens)
                        return
                    
                    # 2. Silence Directive: [NO_RESPONSE]
                    if "[NO_RESPONSE]" in response:
                        logger.info(f"LLM triggered [NO_RESPONSE] on iteration {iteration}.")
                        await safe_cancel_status(status_task)
                        if status_data: status_data["task"] = None
                        if loading_msg:
                            try:
                                await loading_msg.delete()
                            except:
                                pass
                        return
                    

                    # 3. Legacy Mode Switch: [MODE: think] — only active when AUTO_THINKING is OFF
                    if not config.AUTO_THINKING and "[MODE: think]" in response and not think_enabled:
                        logger.info("Model requested Legacy Thinking Mode.")
                        think_enabled = True
                        curr_phrases = PHRASES_HYBRID if curr_phrases in (PHRASES_SEARCH, PHRASES_ACTION) else PHRASES_THINK

                        clean_resp = response.replace("[MODE: think]", "").strip()
                        if clean_resp:
                            messages.append({"role": "assistant", "content": clean_resp})
                        messages.append({"role": "system", "content": "[DIRECTIVE]: You are now in Thinking Mode. Provide a detailed, step-by-step reasoning chain before your final answer."})
                        continue

                    # 4. Action: [ACTION: tool(args)]
                    action_match = re.search(r'\[ACTION:\s*(\w+)(?:\s*\((.*?)\))?\]', response)
                    if not action_match:
                        # Fallback if the LLM forgets the [ACTION:] wrapper but explicitly typed the tool name
                        action_match = None
                        
                    if action_match:
                        tool_name = action_match.group(1).lower()
                        tool_args = action_match.group(2) or ""
                        tool_id = f"{tool_name}:{tool_args}"
                        
                        if tool_id in executed_tools:
                            logger.warning(f"Model repeated tool call: {tool_id}. Breaking loop.")
                            break
                            
                        logger.info(f"Model triggered tool: {tool_name}({tool_args})")
                        executed_tools.add(tool_id)
                        
                        # Update phrases for the next iteration
                        curr_phrases = PHRASES_HYBRID if config.AUTO_THINKING else PHRASES_ACTION

                        tool_result = await self._dispatch_tool(tool_name, tool_args, message, loading_msg)
                        
                        if tool_result == "HANDLED_UI":
                            await safe_cancel_status(status_task)
                            return
                            
                        messages.append({"role": "assistant", "content": response})
                        messages.append({"role": "user", "content": f"[TOOL_RESULT]: {tool_result}"})
                        continue


                    # 5. Final Response formatting
                    await safe_cancel_status(status_task)
                    if status_data: status_data["task"] = None
                    
                    # Note: accumulated_thought is kept internally for quality but not displayed per user preference
                    await self._send_safe_response(
                        loading_msg, 
                        response, 
                        message, 
                        tokens=session_tokens, 
                        tps=last_tps, 
                        show_stats=show_stats, 
                        used_model=llm_res.get("model", ""),
                        variation=variation
                    )
                    increment_stats(messages=1, tokens=session_tokens)
                    return

                except Exception as e:
                    logger.error(f"Error in agent loop iteration {iteration}: {e}")
                    # On fatal LLM logic errors, break the loop to avoid infinite stuck states
                    break

        except Exception as e:
            logger.error(f"Error in process_queued_prompt: {e}", exc_info=True)
            try:
                err_text = f"⚠️ **Critical Error:** {str(e)[:1800]}"
                if loading_msg:
                    await loading_msg.edit(content=err_text)
                else:
                    await message.reply(err_text)
            except:
                pass
        finally:
            # ALWAYS ensure the typing/loading status is cancelled
            await safe_cancel_status(status_task)

    async def _send_safe_response(self, loading_msg: discord.Message | None, content: str, original_msg: discord.Message, tokens: int = 0, tps: float = 0.0, show_stats: bool = False, used_model: str = "", variation: str = "default"):
        """Helper to send responses that might exceed Discord's 2000 character limit, prioritizing newline splits.
        
        When loading_msg is None (SHOW_LOADING_MESSAGES=false), sends a fresh reply instead of editing.
        """
        import re
        from database import get_stats

        # 1. Append Stats Footer if requested
        if show_stats and tokens > 0:
            stats = get_stats()
            total_tokens = stats.get("tokens_used", 0)
            
            # Format total (e.g. 1.2k, 1.5M)
            if total_tokens > 1_000_000:
                total_str = f"{total_tokens / 1_000_000:.1f}M"
            elif total_tokens > 1_000:
                total_str = f"{total_tokens / 1_000:.1f}k"
            else:
                total_str = str(total_tokens)
            if used_model and "gemini" in used_model.lower():
                footer = f"\n-# `{used_model} · {tokens} tokens · {total_str} total`"
            else:
                footer = f"\n-# `{tps:.1f} t/s · {tokens} tokens · {total_str} total`"
            content += footer
        
        # Clean up any leaked system tags or headers the LLM might hallucinate
        # Catches: [TOOL_RESULT], [system], ### [REPLIED TO CONTEXT]:, ### [USER PROMPT]:
        content = re.sub(r'(?:###\s*)?\[(?i:tool_result|system|sys|replied to context|user prompt(?: end)?)\]:?\s*', '', content)
        content = content.replace("### :", "").strip() # Edge case cleanup

        # Pre-process content: strip out redundant protocol from markdown link display text
        # e.g., converts [https://tx24.is-a.dev/](https://tx24.is-a.dev/) to [tx24.is-a.dev](https://tx24.is-a.dev/)
        def clean_link(match):
            display_text = match.group(1)
            url = match.group(2)
            if display_text.startswith("http://") or display_text.startswith("https://"):
                import re
                clean_text = re.sub(r'^https?://(www\.)?', '', display_text)
                if clean_text.endswith('/'):
                    clean_text = clean_text[:-1]
                return f"[{clean_text}]({url})"
            return match.group(0)
            
        import re
        content = re.sub(r'\[(.*?)\]\((https?://.*?)\)', clean_link, content)

        # Split into chunks (Discord 2000-char limit)
        if len(content) <= 2000:
            chunks = [content]
        else:
            chunks = []
            remaining = content
            while len(remaining) > 0:
                if len(remaining) <= 1900:
                    chunks.append(remaining)
                    break
                split_idx = remaining.rfind('\n', 0, 1900)
                if split_idx == -1:
                    split_idx = remaining.rfind(' ', 0, 1900)
                if split_idx == -1:
                    split_idx = 1900
                chunks.append(remaining[:split_idx].strip())
                remaining = remaining[split_idx:].strip()

        if not chunks:
            chunks = [""]
            
        discord_files = []

        # Send chunks
        for i, chunk in enumerate(chunks):
            # Only attach files to the final chunk
            files_to_attach = discord_files if i == len(chunks) - 1 else []
            
            try:
                if i == 0:
                    if loading_msg:
                        if files_to_attach:
                            await loading_msg.edit(content=chunk, attachments=files_to_attach)
                        else:
                            await loading_msg.edit(content=chunk)
                        if variation and variation != 'default':
                            save_message_variation(loading_msg.id, variation)
                    else:
                        if files_to_attach:
                            sent = await original_msg.reply(chunk, mention_author=False, files=files_to_attach)
                        else:
                            sent = await original_msg.reply(chunk, mention_author=False)
                        if sent and variation and variation != 'default':
                            save_message_variation(sent.id, variation)
                else:
                    if files_to_attach:
                        sent = await original_msg.channel.send(chunk, files=files_to_attach)
                    else:
                        sent = await original_msg.channel.send(chunk)
                    if sent and variation and variation != 'default':
                        save_message_variation(sent.id, variation)
                    await asyncio.sleep(0.8)
            except Exception as e:
                logger.error(f"Failed to send/edit message chunk {i}: {e}")

    async def _dispatch_tool(self, name: str, args: str, message: discord.Message, loading_msg: discord.Message) -> str:
        """Helper to execute tools and return a string result for the LLM."""
        try:
            # Clean quotes from args
            clean_args = args.strip(' "\'')
            
            if name == "search":
                if config.SHOW_LOADING_MESSAGES:
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
                    
            elif name in ["reminder", "set_reminder"]:
                # Expecting format: time, "topic" (e.g. 60, "test" or "10m", "test")
                try:
                    # Robust argument splitting
                    first_comma = clean_args.find(',')
                    if first_comma != -1:
                        time_str = clean_args[:first_comma].strip(' "')
                        topic = clean_args[first_comma+1:].strip(' "')
                    else:
                        time_str = clean_args.strip(' "')
                        topic = "Reminder"

                    # 1. Try our new duration parser (handles 10m, 1h, etc.)
                    from tools import parse_duration
                    secs = await parse_duration(time_str)
                    
                    # 2. Fallback to math evaluator (handles 4*60*60)
                    if secs is None:
                        from tools import calculate_math
                        math_res = await calculate_math(time_str)
                        if math_res['status'] == 'success':
                            secs = int(float(math_res['result']))
                    
                    # 3. Final default
                    if secs is None:
                        secs = 60 # Default fallback
                    
                    trigger = int(time.time()) + secs
                    add_reminder(message.channel.id, message.id, trigger, topic)
                    
                    # Human-readable time
                    if secs >= 86400:
                        time_desc = f"{secs/86400:.1f} days"
                    elif secs >= 3600:
                        time_desc = f"{secs/3600:.1f} hours"
                    elif secs >= 60:
                        time_desc = f"{secs/60:.1f} minutes"
                    else:
                        time_desc = f"{secs} seconds"

                    reply_text = f"> ⏰ **Timer Set:** I will remind you in **{time_desc}** (at <t:{trigger}:T>)."
                    if loading_msg:
                        await loading_msg.edit(content=reply_text)
                    else:
                        await message.reply(reply_text)
                    increment_stats(tools=1, messages=1)
                    return "HANDLED_UI"
                except Exception as e:
                    logger.error(f"Error setting reminder: {e}")
                    return f"Error setting reminder: {e}"

                
            elif name == "memory_save":
                # Expecting format: target, "key", "value" (target can be id or keyword)
                parts = [p.strip(' "\'') for p in clean_args.split(",")]
                
                # Check if the first part is a potential ID (numeric) or a keyword
                if len(parts) >= 2:
                    first_arg = parts[0]
                    if first_arg.isdigit():
                        # Target is a User ID
                        target_id = int(first_arg)
                        key = parts[1] if parts[1] else "note"
                        val = parts[2] if len(parts) > 2 else ""
                        save_memory(target_id, key, val)
                        
                        target_name = "you"
                        if target_id != message.author.id:
                            member = message.guild.get_member(target_id) if message.guild else None
                            target_name = member.display_name if member else f"User {target_id}"
                        reply_text = f"> 🧠 **Memory Saved:** I've taken a note about {target_name}."
                    else:
                        # Target is a Keyword
                        keyword = first_arg
                        val = parts[1] if parts[1] else ""
                        # If a third arg exists, join it to value or treat parts[1] as key?
                        # For keywords, the tool description says memory_save("keyword", "value")
                        save_keyword_memory(keyword, val)
                        reply_text = f"> 🧠 **Keyword Saved:** I'll remember that when '{keyword}' is mentioned."
                else:
                    # Fallback to simple note for author
                    save_memory(message.author.id, "note", parts[0] if parts else "")
                    reply_text = f"> 🧠 **Memory Saved:** I've taken a note for you."

                if loading_msg:
                    await loading_msg.edit(content=reply_text)
                else:
                    await message.reply(reply_text)
                increment_stats(tools=1, messages=1)
                return "HANDLED_UI"
                
            elif name == "summarize":
                limit = int(clean_args) if clean_args.isdigit() else config.CHANNEL_HISTORY_LIMIT
                history = [msg async for msg in message.channel.history(limit=limit, before=message) if msg.author.id != self.user.id]
                transcript = "\n".join([f"{m.author.name}: {m.content}" for m in reversed(history)])
                return f"Transcribed History:\n{transcript}"
                
                
            elif name == "fetch_url":
                from tools import fetch_url
                if config.SHOW_LOADING_MESSAGES:
                    await loading_msg.edit(content=f"> 🌐 ***Fetching URL content...***")
                res = await fetch_url(clean_args, client=self.ollama_http_client)
                increment_stats(tools=1)
                if res.get("status") == "success":
                    return f"Page Content (truncated to useful portion):\n{res['content']}"
                return f"Failed to fetch URL: {res.get('message', 'Unknown Error')}"
                
            return f"Error: Tool '{name}' not found."
        except Exception as e:
            logger.error(f"Error executing tool {name}: {str(e)}", exc_info=True)
            return f"Error executing tool {name}: {str(e)}"
