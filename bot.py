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
    400: ("Invalid Argument", "Double check your request, something seems invalid!"),
    401: ("Unauthenticated", "Authentication failed. My API key might be invalid."),
    403: ("Permission Denied", "Permission denied. I don't have access to this resource."),
    404: ("Not Found", "Model not found. It might be deprecated or non-existent."),
    429: ("Resource Exhausted", "I'm a bit overwhelmed right now, please try again in a minute!"),
    500: ("Internal Error", "Internal server error. Google's AI is having a moment."),
    503: ("Service Unavailable", "Service unavailable. The AI is likely down or overloaded."),
    504: ("Gateway Timeout", "Gateway timeout. The request took way too long."),
}

from context_builder import clean_mention, build_context
from llm_client import ask_llm
from gemini_client import get_client

from guardrails import is_safe_prompt
from database import (
    init_db, add_reminder, get_due_reminders, delete_reminder, save_memory, 
    get_memories, increment_stats, get_stats, save_user_variation, 
    get_user_settings, get_message_variation, save_message_variation,
    save_system_state, get_system_state, save_keyword_memory, get_keyword_memories
)
import time
import urllib.parse
import json
import re
import os

# ---------------------------------------------------------------------------
# File attachment security policy
# ---------------------------------------------------------------------------
# Only these plain-text extensions are allowed.  Everything else is blocked.
ALLOWED_TEXT_EXTENSIONS: set[str] = {
    ".txt", ".log", ".md", ".markdown", ".rst",
    ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env",
    ".xml", ".html", ".htm", ".css",
    ".diff", ".patch", ".ttml",
    # Programming Languages & Scripts
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".cs", ".go", ".rs", ".swift", ".kt", ".kts",
    ".rb", ".rbw", ".pl", ".pm", ".php", ".phtml",
    ".lua", ".r", ".rscript", ".java", ".sql", ".dart",
}

# Hard block list – binaries, compiled code, or containers.
# This ensures the bot doesn't attempt to read non-text data.
BLOCKED_EXTENSIONS: set[str] = {
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear",
    ".exe", ".dll", ".so", ".dylib", ".elf", ".bin", ".out", ".run",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".iso", ".img", ".dmg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}

MAX_ATTACHMENT_BYTES = 50_000  # Bytes per file
MAX_ATTACHMENT_FILES = 5

# Multimedia attachments — handled natively via Gemini
ALLOWED_MEDIA_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp4", ".mp3", ".wav", ".flac", ".ogg", ".avi", ".mkv", ".mov", ".webm", ".heic"
}
MAX_MEDIA_BYTES = 10_000_000  # 10 MB per file
MAX_MEDIA_FILES = 3

async def read_text_attachments(attachments: list[discord.Attachment]) -> tuple[str | None, str | None]:
    """
    Download and validate text attachments from a Discord message.
    
    Returns:
        (combined_text, error_message)  – exactly one will be non-None.
    """
    if not attachments:
        return None, None

    # Respect the per-message file cap
    attachments = attachments[:MAX_ATTACHMENT_FILES]

    parts: list[str] = []
    for att in attachments:
        ext = os.path.splitext(att.filename)[1].lower()

        # Block executables / binaries first (deny always wins)
        if ext in BLOCKED_EXTENSIONS:
            return None, (
                f"🚫 **Blocked:** `{att.filename}` has a potentially executable extension (`{ext}`). "
                "I cannot read files that could be executed for security reasons."
            )

        # Only accept explicitly allowed plain-text types
        if ext not in ALLOWED_TEXT_EXTENSIONS:
            return None, (
                f"❌ **Unsupported file type:** `{att.filename}` (`{ext}`). "
                f"Allowed plain-text types: {', '.join(sorted(ALLOWED_TEXT_EXTENSIONS))}"
            )

        # Size guard
        if att.size > MAX_ATTACHMENT_BYTES:
            size_kb = att.size // 1024
            return None, (
                f"❌ **File too large:** `{att.filename}` is {size_kb} KB. "
                f"Maximum size per file is {MAX_ATTACHMENT_BYTES // 1024} KB."
            )

        # Download the file content
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(att.url)
                resp.raise_for_status()
                text = resp.text
        except Exception as e:
            return None, f"❌ **Failed to read** `{att.filename}`: {e}"

        parts.append(f"### Attached file: `{att.filename}`\n```\n{text}\n```")

    if not parts:
        return None, None

    combined = "\n\n".join(parts)
    return combined, None


async def read_media_attachments(
    attachments: list[discord.Attachment]
) -> tuple[list[dict] | None, str | None]:
    """
    Download and validate media attachments from a Discord message.

    Returns:
        (list_of_media_dicts, error_message) — exactly one will be non-None.
        Each dict has 'mime_type' and 'data' (bytes).
    """
    import mimetypes
    
    media_atts = [
        a for a in attachments
        if os.path.splitext(a.filename)[1].lower() in ALLOWED_MEDIA_EXTENSIONS or getattr(a, "is_voice_message", lambda: False)()
    ][:MAX_MEDIA_FILES]

    if not media_atts:
        return None, None

    media_list: list[dict] = []
    for att in media_atts:
        if att.size > MAX_MEDIA_BYTES:
            size_mb = att.size / 1_000_000
            return None, (
                f"❌ **Media too large:** `{att.filename}` is {size_mb:.1f} MB. "
                f"Maximum per file is {MAX_MEDIA_BYTES // 1_000_000} MB."
            )

        # Download the raw bytes
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(att.url)
                resp.raise_for_status()
                mime_type, _ = mimetypes.guess_type(att.filename)
                if getattr(att, "is_voice_message", lambda: False)():
                    mime_type = "audio/ogg"
                elif not mime_type:
                    # fallback
                    ext = os.path.splitext(att.filename)[1].lower()
                    if ext == ".mp4": mime_type = "video/mp4"
                    elif ext == ".mp3": mime_type = "audio/mp3"
                    elif ext == ".wav": mime_type = "audio/wav"
                    elif ext == ".ogg": mime_type = "audio/ogg"
                    elif ext == ".webm": mime_type = "video/webm"
                    elif ext == ".mov": mime_type = "video/quicktime"
                    else: mime_type = "image/jpeg"
                media_list.append({"mime_type": mime_type, "data": resp.content, "filename": att.filename})
        except Exception as e:
            return None, f"❌ **Failed to download** `{att.filename}`: {e}"

    if not media_list:
        return None, None

    return media_list, None


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
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            stats = get_stats()
            servers = len(bot.guilds)
            msgs = stats['messages_answered']
            tokens = stats['tokens_used']
            
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
                    
                elif sub == "model":
                    if len(parts) < 3:
                        await message.reply("> ❌ **Usage:** `;gem model <model_id>`")
                        return
                    new_model = parts[2].strip()
                    
                    # Active Model Validation
                    validate_msg = await message.reply(f"> 🔍 **Validating model:** `{new_model}`...")
                    try:
                        genai_client = get_client()
                        await genai_client.aio.models.get(model=new_model)
                        config.update_config("GEMINI_MODEL", new_model)
                        await validate_msg.edit(content=f"> ✅ **Model validated and changed to:** `{new_model}`")
                    except Exception as e:
                        await validate_msg.edit(content=f"> ❌ **Invalid Model:** `{new_model}` is not accessible or does not exist.\n> *Error: {str(e)[:300]}*")
                    return
                    
                elif sub == "autothink":
                    if len(parts) < 3:
                        await message.reply("> ❌ **Usage:** `;gem autothink <on/off>`")
                        return
                    val = get_toggle_val(parts[2])
                    config.update_config("AUTO_THINKING", val)
                    await message.reply(f"> ✅ **Auto-Thinking set to:** `{val == 'true'}`")
                    return
                    
                elif sub == "vertex":
                    if len(parts) < 3:
                        await message.reply("> ❌ **Usage:** `;gem vertex <on/off>`")
                        return
                    is_on = parts[2].lower() in ("on", "true", "yes")
                    config.update_config("USE_VERTEX_AI", "true" if is_on else "false")
                    await message.reply(f"> ✅ **Vertex AI set to:** `{is_on}`")
                    return
                    
                elif sub == "pause":
                    config.update_config("IS_PAUSED", "true")
                    await message.reply("> ⏸️ **Bot Paused.**")
                    return
                    
                elif sub == "resume":
                    config.update_config("IS_PAUSED", "false")
                    await message.reply("> ▶️ **Bot Resumed.**")
                    return
                    
                elif sub == "statusmsg" or sub == "loading":
                    if len(parts) < 3:
                        await message.reply("> ❌ **Usage:** `;gem statusmsg <on/off>`")
                        return
                    val = get_toggle_val(parts[2])
                    config.update_config("SHOW_LOADING_MESSAGES", val)
                    await message.reply(f"> ✅ **Status Messages set to:** `{val == 'true'}`")
                    return
                    


                elif sub == "queue":
                    if len(parts) < 3:
                        await message.reply(f"> ⏳ **Queue Status:** `{'Enabled' if config.ENABLE_QUEUE else 'Disabled'}`\n> **Usage:** `;gem queue <on/off>`")
                        return
                    val = get_toggle_val(parts[2])
                    config.update_config("ENABLE_QUEUE", val)
                    await message.reply(f"> ✅ **Queue System set to:** `{val == 'true'}`")
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
                        "- `;gem model <id>`: Switch Gemini model.\n"
                        "- `;gem autothink <on/off>`: Toggle native reasoning.\n"
                        "- `;gem statusmsg <on/off>`: Toggle loading messages.\n"
                        "- `;gem vertex <on/off>`: Toggle Vertex AI mode.\n"
                        "- `;gem queue <on/off>`: Toggle prompt queue.\n"
                        "- `;gem pause/resume`: Control bot processing.\n"
                        "- `;gem join <invite>`: Join a Discord server.\n"
                        "- `;gem restart`: Force PM2 process restart.\n\n"
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
            
            if sub == "prompts" or (sub == "prompt" and len(parts) < 3):
                settings = get_user_settings(message.author.id)
                current_var = settings.get('variation', 'default')
                
                desc_lines = []
                for k, v in config.PROMPT_DESCRIPTIONS.items():
                    active_marker = " 🟢" if k == current_var else ""
                    desc_lines.append(f"- **{k.capitalize()}**: {v}{active_marker}")
                
                final_msg = "> 📜 **Available Prompt Variations**\n\n" + "\n".join(desc_lines) + "\n\n> **Usage:** `;gem prompt <name>`"
                
                if len(final_msg) > 1950:
                    parts_msg = [final_msg[i:i+1900] for i in range(0, len(final_msg), 1900)]
                    for p in parts_msg: await message.reply(p)
                else: await message.reply(final_msg)
                return

            elif sub == "prompt":
                if len(parts) < 3:
                    await message.reply("> ❌ **Missing variation name.** Use `;gem prompts` to see the list.")
                    return
                    
                target = parts[2].lower()
                if target in config.PROMPT_MODIFIERS:
                    target_user = message.author
                    is_owner = message.author.id == 504541573636161546
                    
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
                            
                    save_user_variation(target_user.id, target)
                    if target_user.id == message.author.id:
                        await message.reply(f"> ✅ **Personality shifted to:** `{target.capitalize()}`")
                    else:
                        await message.reply(f"> ✅ **Personality for {target_user.name} shifted to:** `{target.capitalize()}`")
                else:
                    await message.reply(f"> ❌ **Unknown variation:** `{target}`. Use `;gem prompts` to see the list.")
                return

            elif sub == "help" and message.author.id != 504541573636161546:
                # Public restricted help
                help_text = (
                    "> 🎭 **Gemini Persona Commands**\n"
                    "- `;gem prompt <name>`: Switch the bot's personality for yourself.\n"
                    "- `;gem prompts`: List all available personalities.\n"
                    "- `;gem status`: Show current personality and system status.\n"
                    "- `;gem help`: Show this message."
                )
                await message.reply(help_text)
                return

            elif sub == "status":
                mode = "Vertex AI" if config.USE_VERTEX_AI else "AI Studio"
                paused_status = "⏸️ PAUSED" if config.IS_PAUSED else "▶️ RUNNING"
                think_status = "🧠 ON" if config.AUTO_THINKING else "⚪ OFF"
                queue_status = "⏳ ON" if config.ENABLE_QUEUE else "⚪ OFF"
                
                # Fetch user-specific settings
                user_settings = get_user_settings(message.author.id)
                variation = user_settings.get('variation', 'default').capitalize()
                
                await message.reply(
                    f"> 📊 **System Status**\n"
                    f"- **Model:** `{config.GEMINI_MODEL}`\n"
                    f"- **Variation:** `{variation}`\n"
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

        # 3. Handle Text Attachments
        text_atts = [a for a in all_attachments if os.path.splitext(a.filename)[1].lower() in ALLOWED_TEXT_EXTENSIONS]
        if text_atts:
            attachments_text, att_error = await read_text_attachments(text_atts)
            if att_error:
                await message.reply(f"> {att_error}")
                return
            if attachments_text:
                logger.info(f"Loaded {len(text_atts)} text attachment(s)")

        # 4. Handle Media Attachments (Images/Audio/Video Native Direct and Voice Messages)
        media_atts = [a for a in all_attachments if os.path.splitext(a.filename)[1].lower() in ALLOWED_MEDIA_EXTENSIONS or getattr(a, "is_voice_message", lambda: False)()]
        media_data = None
        if media_atts:
            # Run image/media download (native)
            media_data, img_err = await read_media_attachments(media_atts)
            if img_err:
                await message.reply(f"> ❌ **Media Download Failed:** {img_err}")
                return
            
            logger.info(f"Prepared {len(media_atts)} media file(s) for direct multimodal inference")

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
        async for msg in message.channel.history(limit=50):
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
            
            # Fetch current user personality
            settings = get_user_settings(message.author.id)
            variation = settings.get('variation', 'default')
            
            # Override with reply chain context if replying to the bot
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
                    increment_stats(messages=1)
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
                limit = int(clean_args) if clean_args.isdigit() else 50
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
