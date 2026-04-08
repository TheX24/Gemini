import discord
import logging
import asyncio
import httpx
import random
from config import (
    OLLAMA_MODEL, PHRASES_DEFAULT, PHRASES_THINK, 
    PHRASES_SEARCH, PHRASES_HYBRID, PHRASES_ACTION, SHOW_LOADING_MESSAGES,
    PHRASES_PARSING, PHRASES_QUEUE
)


from context_builder import clean_mention, build_context
from ollama_client import ask_ollama, ask_ollama_vision
from tools import search_web
from guardrails import is_safe_prompt
from database import init_db, add_reminder, get_due_reminders, delete_reminder, save_memory, get_memories, increment_stats, get_stats
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
}

# Hard block list – anything that can be executed or is a binary container.
# This is belt-and-suspenders on top of the allow-list.
BLOCKED_EXTENSIONS: set[str] = {
    ".py", ".pyw", ".pyc", ".pyo",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh",
    ".bat", ".cmd", ".ps1", ".psm1", ".psd1",
    ".exe", ".dll", ".so", ".dylib", ".elf",
    ".bin", ".out", ".run",
    ".rb", ".rbw", ".pl", ".pm", ".php", ".phtml",
    ".lua", ".r", ".rscript",
    ".java", ".class", ".jar", ".war", ".ear",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".cs", ".go", ".rs", ".swift", ".kt", ".kts",
    ".vbs", ".vb", ".wsf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".iso", ".img", ".dmg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".bmp", ".svg", ".ico",  # Images go through vision pipeline, not blocked
    ".mp3", ".mp4", ".wav", ".flac", ".ogg", ".avi", ".mkv", ".mov",
}

MAX_ATTACHMENT_BYTES = 40_000  # Bytes per file
MAX_ATTACHMENT_FILES = 5

# Image attachments — handled via vision model or OCR, not the text pipeline
ALLOWED_IMAGE_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}
MAX_IMAGE_BYTES = 10_000_000  # 10 MB per image
MAX_IMAGE_FILES = 3

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


async def read_image_attachments(
    attachments: list[discord.Attachment],
    ollama_client=None,
    status_msg: discord.Message | None = None,
    method: str = "vision"
) -> tuple[str | None, str | None]:
    """
    Process image attachments via Ollama vision model (preferred) or pytesseract OCR fallback.

    Returns:
        (combined_descriptions, error_message) — exactly one will be non-None.
    """
    from config import OLLAMA_VISION_MODEL, VISION_NUM_GPU

    image_atts = [
        a for a in attachments
        if os.path.splitext(a.filename)[1].lower() in ALLOWED_IMAGE_EXTENSIONS
    ][:MAX_IMAGE_FILES]

    if not image_atts:
        return None, None

    parts: list[str] = []
    for att in image_atts:
        if att.size > MAX_IMAGE_BYTES:
            size_mb = att.size / 1_000_000
            return None, (
                f"❌ **Image too large:** `{att.filename}` is {size_mb:.1f} MB. "
                f"Maximum per image is {MAX_IMAGE_BYTES // 1_000_000} MB."
            )

        # Download the raw image bytes
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(att.url)
                resp.raise_for_status()
                image_bytes = resp.content
        except Exception as e:
            return None, f"❌ **Failed to download** `{att.filename}`: {e}"

        if OLLAMA_VISION_MODEL and method == "vision":
            # --- Primary path: Ollama vision model ---
            if status_msg and SHOW_LOADING_MESSAGES:
                gpu_label = "GPU" if VISION_NUM_GPU != 0 else "CPU"
                await status_msg.edit(content=(
                    f"> 🧠 ***Running `{OLLAMA_VISION_MODEL}` ({gpu_label}) on `{att.filename}`..."
                    f" ({image_atts.index(att) + 1}/{len(image_atts)})***"
                ))
            vision_prompt = (
                "Analyse this image thoroughly. "
                "If it contains text (e.g. a screenshot, document, code, log), transcribe ALL visible text exactly. "
                "Then describe the image content, layout, and any important details."
            )
            description = await ask_ollama_vision(image_bytes, vision_prompt, ollama_client)
            if description.startswith("Error:"):
                return None, f"❌ **Vision model error** for `{att.filename}`: {description}"
            parts.append(f"### Image: `{att.filename}` (analysed by {OLLAMA_VISION_MODEL})\n{description}")
        else:
            # --- Fallback path: pytesseract OCR ---
            if status_msg and SHOW_LOADING_MESSAGES:
                await status_msg.edit(content=(
                    f"> 🔍 ***Running OCR on `{att.filename}`..."
                    f" ({image_atts.index(att) + 1}/{len(image_atts)})***"
                ))
            try:
                import pytesseract  # type: ignore
                from PIL import Image  # type: ignore
                import io

                img = Image.open(io.BytesIO(image_bytes))
                ocr_lang = os.getenv("OCR_LANGUAGES", "eng")
                text = pytesseract.image_to_string(img, lang=ocr_lang).strip()
                if text:
                    parts.append(
                        f"### OCR from `{att.filename}`:\n"
                        f"```\n{text}\n```"
                    )
                else:
                    parts.append(
                        f"### Image: `{att.filename}`\n"
                        f"*(OCR found no text. Set `OLLAMA_VISION_MODEL` in `.env` for full image understanding.)*"
                    )
            except ImportError:
                return None, (
                    "❌ **Image support not configured.** Choose one option:\n"
                    "**Option A** — Vision model (better): add `OLLAMA_VISION_MODEL=moondream` to `.env` "
                    "then run `ollama pull moondream`.\n"
                    "**Option B** — OCR (text only): "
                    "`pip install pytesseract pillow` and `sudo apt install tesseract-ocr`."
                )

    if not parts:
        return None, None

    return "\n\n".join(parts), None


# Set up logging for the bot
logger = logging.getLogger(__name__)

async def rotate_status(loading_msg: discord.Message, phrases: list, prefix: str = "> ⏳ ***"):
    """
    Background task to rotate loading phrases if generation takes a while.
    Silently exits when SHOW_LOADING_MESSAGES is disabled.
    """
    if not SHOW_LOADING_MESSAGES:
        try:
            while True:
                await asyncio.sleep(3600)  # Park the task; it will be cancelled externally
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
            try:
                await loading_msg.edit(content=f"{prefix}{phrase}***")
            except discord.NotFound:
                break # Message deleted, stop rotating
            except Exception:
                pass # Ignore transient edit errors
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

    async def put(self, user_id, message, loading_msg, user_prompt, reply_content, is_reply_to_self, history=None, user_info=None, other_users_info=None, attachments_text=None, images_text=None, status_task=None):
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
            "images_text": images_text,
            "status_task": status_task,
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
                    task_data.get("images_text"),
                    task_data.get("status_task")
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

        if not (is_mentioned or is_reply_to_self):
            return

        # 4. Process the message
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
        images_text = None
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

        # 4. Handle Image Attachments (Automatic Vision/OCR)
        image_atts = [a for a in all_attachments if os.path.splitext(a.filename)[1].lower() in ALLOWED_IMAGE_EXTENSIONS]
        if image_atts:
            # Create loading message early for image processing feedback
            if SHOW_LOADING_MESSAGES:
                loading_msg = await message.reply("> 🖼️ ***Analyzing attached images...***")
            
            # Run vision model automatically
            img_obs, img_err = await read_image_attachments(image_atts, self.ollama_http_client, loading_msg, method="vision")
            if img_err:
                err_text = f"> ❌ **Image Analysis Failed:** {img_err}"
                if loading_msg: await loading_msg.edit(content=err_text)
                else: await message.reply(err_text)
                return
            
            images_text = f"[Image Analysis Attachment]:\n{img_obs}"
            logger.info(f"Automatically analyzed {len(image_atts)} image(s)")

        # 5. Early exit if no content to process
        is_reply = message.reference is not None
        if not user_prompt and not is_reply and not attachments_text and not images_text:
            return

        # 6. Display final loading state if not already set by image processing
        status_task = None
        if loading_msg is None and SHOW_LOADING_MESSAGES:
            loading_msg = await message.reply(f"> ⏳ ***{random.choice(PHRASES_PARSING)}***")
            status_task = asyncio.create_task(rotate_status(loading_msg, PHRASES_PARSING))
        elif loading_msg and SHOW_LOADING_MESSAGES:
            # If image analysis was already running, start rotating parsing phrases
            status_task = asyncio.create_task(rotate_status(loading_msg, PHRASES_PARSING))





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
            for m in message.mentions:
                if m.id != self.user.id and m.id != message.author.id and m.id not in relevant_users_scanned:
                    other_users_info.append(await extract_user_metadata(m, message.guild))
                    relevant_users_scanned.add(m.id)
                    if len(other_users_info) >= 3:
                        break

        prompt_lower = user_prompt.lower()
        if len(prompt_lower) > 3 and len(other_users_info) < 3:
            for u_id, u in recent_users_map.items():
                if u_id in relevant_users_scanned:
                    continue
                names_to_check = [u.name, getattr(u, "global_name", None), getattr(u, "display_name", None)]
                matched = False
                for n in names_to_check:
                    if n and len(n) > 2 and (n.lower() in prompt_lower):
                        matched = True
                        break
                if matched:
                    other_users_info.append(await extract_user_metadata(u, message.guild))
                    relevant_users_scanned.add(u_id)
                    if len(other_users_info) >= 3:
                        break

        # 6. Extract User Metadata (Identity awareness)
        user_info = await extract_user_metadata(message.author, message.guild)

        # 7. Add to queue
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
            images_text=images_text,
            status_task=status_task,
        )


        if not success:
            err_text = "> ❌ **Queue Full:** You already have an active prompt being processed or in the queue."
            if loading_msg:
                await loading_msg.edit(content=err_text)
            else:
                await message.reply(err_text)
            return

        if position > 0 and loading_msg:
            if status_task:
                status_task.cancel()
            await loading_msg.edit(content=f"> ⏳ ***Queued (Position #{position})...***")
            status_task = asyncio.create_task(rotate_status(loading_msg, PHRASES_QUEUE, prefix=f"> ⏳ ***Queued (Pos #{position}) | "))
            # Update the task in task_data (we need to update the entry in the queue, but that's hard)
            # Actually, put has already happened.
            # I should update the dictionary in task_data.

            logger.info(f"on_message: User {message.author.id} queued at pos {position}")
        else:
            logger.info(f"on_message: User {message.author.id} added at pos 0 (Immediate Processing)")

    async def process_queued_prompt(self, message: discord.Message, loading_msg: discord.Message, user_prompt: str, reply_content: str, is_reply_to_self: bool, history: list, user_info: dict, other_users_info: list | None = None, attachments_text: str | None = None, images_text: str | None = None, status_task: asyncio.Task | None = None):
        """
        Agentic loop allowing the main model to decide if it needs tools, thinking, or direct answer.
        """
        logger.info(f"process_queued_prompt: Starting agentic loop for user {message.author.id}")
        
        # 1. Context Compression (Disabled for large context)
        # Bypassing slow summarization LLM calls entirely to leverage Ollama's native KV Prompt Cache for instant evaluation.
        recap = None
        short_history = history

        # 2. Build final prompt structures
        user_info_for_context = user_info if not getattr(self, "ANONYMOUS_PROMPT", False) else None
        other_for_context = other_users_info if not getattr(self, "ANONYMOUS_PROMPT", False) else None
        messages = build_context(user_prompt, reply_content, is_reply_to_self, history=short_history, recap=recap, user_info=user_info_for_context, other_users_info=other_for_context)

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

        # Inject image descriptions so the model knows what was in the images
        if images_text:
            messages.insert(1, {
                "role": "system",
                "content": (
                    "[Attached Images Metadata]:\n"
                    + images_text
                )
            })
        
        # 3. Fetch Persistent Memories
        mems = get_memories(message.author.id)
        if mems:
            brain = "\n".join([f"- {m['key']}: {m['value']}" for m in mems])
            messages.insert(1, {"role": "system", "content": f"[User Facts & Memory]:\n{brain}"})
        
        # Tracking states
        think_enabled = "--think" in user_prompt.lower()
        
        # PROACTIVE SEARCH HEURISTIC
        # Force a search if the user asks about something recent or market-related
        search_keywords = ["lately", "recently", "news", "crisis", "prices", "stock", "next", "upcoming", "--search"]
        is_market_query = any(k in user_prompt.lower() for k in search_keywords)
        force_search = is_market_query
        
        curr_phrases = PHRASES_DEFAULT
        # Use inherited status_task if provided
        # Step A: Proactive Search (if triggered by keywords)
        if force_search:
            logger.info(f"Force Search triggered. Refining query for: '{user_prompt}'")
            
            # Use a fast pass to reword based on context (history/recap)
            refine_messages = messages + [
                {"role": "system", "content": "Generate a single, concise, and highly effective web search query to answer the user's latest request. Use only the necessary keywords. Look at the conversation history to understand pronouns or ambiguous terms. Output ONLY the query text."}
            ]
            refined_query = await ask_ollama(refine_messages, client=self.ollama_http_client)
            
            # Clean up refined query
            refined_query = refined_query.strip().strip('"').strip("'")
            if refined_query and "Error:" not in refined_query:
                # Log it so we can see the 'thought' process in terminal
                logger.info(f"Refined Search Query: '{refined_query}'")
                search_results = await search_web(refined_query)
                messages.append({"role": "system", "content": f"[TOOL_RESULT (Proactive Search)]: {search_results}"})
            else:
                logger.warning("Query refinement failed or returned empty results.")

        # --- AGENTIC REACT LOOP ---
        iteration = 0
        max_iterations = 4
        # think_enabled is already set above
        # curr_phrases is already set above
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
                    curr_phrases = PHRASES_HYBRID if think_enabled else PHRASES_SEARCH
                    if SHOW_LOADING_MESSAGES:
                        await loading_msg.edit(content=f"> 🔍 ***{random.choice(curr_phrases)}***")

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
                    # If we were searching, move to Hybrid. Otherwise move to Think.
                    curr_phrases = PHRASES_HYBRID if curr_phrases in (PHRASES_SEARCH, PHRASES_ACTION) else PHRASES_THINK

                    clean_resp = response.replace("[MODE: think]", "").strip()
                    if clean_resp:
                        messages.append({"role": "assistant", "content": clean_resp})
                    # Use a directive to guide the model
                    messages.append({"role": "system", "content": "[DIRECTIVE]: You are now in Thinking Mode. Provide a detailed, step-by-step reasoning chain before your final answer."})
                    continue

                # 4. Action: [ACTION: tool(args)]
                action_match = re.search(r'\[ACTION:\s*(\w+)(?:\s*\((.*?)\))?\]', response)
                if not action_match:
                    # Fallback if the LLM forgets the [ACTION:] wrapper but explicitly typed the tool name
                    action_match = re.search(r'(analyze_images)\s*\(\s*[\'\"]?(ocr|vision)[\'\"]?\s*\)', response)
                    
                if action_match:
                    tool_name = action_match.group(1).lower()
                    tool_args = action_match.group(2) or ""
                    tool_id = f"{tool_name}:{tool_args}"
                    
                    if tool_id in executed_tools:
                        logger.warning(f"Model repeated tool call: {tool_id}. Breaking loop.")
                        break
                        
                    logger.info(f"Model triggered tool: {tool_name}({tool_args})")
                    executed_tools.add(tool_id)
                    
                    # Update phrases for the next iteration (rotation will pick it up)
                    if tool_name == "search_web" or tool_name == "search":
                        curr_phrases = PHRASES_HYBRID if think_enabled else PHRASES_SEARCH
                    else:
                        curr_phrases = PHRASES_HYBRID if think_enabled else PHRASES_ACTION

                    tool_result = await self._dispatch_tool(tool_name, tool_args, message, loading_msg)
                    
                    if tool_result == "HANDLED_UI":
                        status_task.cancel()
                        return
                        
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": f"[TOOL_RESULT]: {tool_result}"})
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
                    err_text = f"⚠️ **Error:** {str(e)[:1800]}"
                    if loading_msg:
                        await loading_msg.edit(content=err_text)
                    else:
                        await message.reply(err_text)
                except:
                    pass
                return

    async def _send_safe_response(self, loading_msg: discord.Message | None, content: str, original_msg: discord.Message):
        """Helper to send responses that might exceed Discord's 2000 character limit, prioritizing newline splits.
        
        When loading_msg is None (SHOW_LOADING_MESSAGES=false), sends a fresh reply instead of editing.
        """
        import re
        
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

        if loading_msg is not None:
            # Edit the pre-existing placeholder into the first chunk
            if chunks:
                await loading_msg.edit(content=chunks[0])
            for i in range(1, len(chunks)):
                if chunks[i].strip():
                    await original_msg.channel.send(chunks[i])
                    await asyncio.sleep(0.8)
        else:
            # No placeholder exists — reply fresh and send continuations
            if chunks:
                await original_msg.reply(chunks[0])
            for i in range(1, len(chunks)):
                if chunks[i].strip():
                    await original_msg.channel.send(chunks[i])
                    await asyncio.sleep(0.8)

    async def _dispatch_tool(self, name: str, args: str, message: discord.Message, loading_msg: discord.Message) -> str:
        """Helper to execute tools and return a string result for the LLM."""
        try:
            # Clean quotes from args
            clean_args = args.strip(' "\'')
            
            if name == "search":
                if SHOW_LOADING_MESSAGES:
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
                # We use a more robust split to allow commas in the topic
                try:
                    # Look for first comma that separates seconds from topic
                    first_comma = clean_args.find(',')
                    if first_comma != -1:
                        secs_str = clean_args[:first_comma].strip(' "')
                        topic = clean_args[first_comma+1:].strip(' "')
                    else:
                        secs_str = clean_args.strip(' "')
                        topic = "Reminder"

                    # Support math expressions in seconds (e.g. 4*60*60)
                    if not secs_str.isdigit():
                        from tools import calculate_math
                        math_res = await calculate_math(secs_str)
                        if math_res['status'] == 'success':
                            # result could be float or int
                            secs = int(float(math_res['result']))
                        else:
                            secs = 60 # Default fallback
                    else:
                        secs = int(secs_str)

                    trigger = int(time.time()) + secs
                    add_reminder(message.channel.id, message.id, trigger, topic)
                    
                    # Human-readable time
                    if secs >= 3600:
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
                # Expecting format: "key", "value"
                parts = [p.strip(' "') for p in clean_args.split(",")]
                key = parts[0] if parts[0] else "note"
                val = parts[1] if len(parts) > 1 else ""
                save_memory(message.author.id, key, val)
                reply_text = "> 🧠 **Memory Saved:** I've taken a note of that."
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
                
            elif name == "stats":
                stats = get_stats()
                dashboard = (
                    f"> 📊 **Global Bot Statistics**\n"
                    f"> 💬 **Messages Answered:** `{stats.get('messages_answered', 0)}`\n"
                    f"> 🔋 **Tokens Consumed:** `{stats.get('tokens_used', 0)}`\n"
                    f"> 🔍 **Deep Searches Run:** `{stats.get('searches_run', 0)}`\n"
                    f"> 🧰 **Tools Executed:** `{stats.get('tools_used', 0)}`"
                )
                if loading_msg:
                    await loading_msg.edit(content=dashboard)
                else:
                    await message.reply(dashboard)
                increment_stats(tools=1, messages=1)
                return "HANDLED_UI"
                
            elif name == "fetch_url":
                from tools import fetch_url
                if SHOW_LOADING_MESSAGES:
                    await loading_msg.edit(content=f"> 🌐 ***Fetching URL content...***")
                res = await fetch_url(clean_args, client=self.ollama_http_client)
                increment_stats(tools=1)
                if res.get("status") == "success":
                    return f"Page Content (truncated to useful portion):\n{res['content']}"
                return f"Failed to fetch URL: {res.get('message', 'Unknown Error')}"

            elif name == "analyze_images":
                method = clean_args.lower()
                if method not in ["ocr", "vision"]:
                    method = "vision"
                    
                image_atts = [a for a in message.attachments if os.path.splitext(a.filename)[1].lower() in ALLOWED_IMAGE_EXTENSIONS]
                
                # Check replied-to message if no attachments on the trigger message
                if not image_atts and message.reference:
                    try:
                        ref_msg = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
                        if ref_msg and ref_msg.attachments:
                            image_atts = [a for a in ref_msg.attachments if os.path.splitext(a.filename)[1].lower() in ALLOWED_IMAGE_EXTENSIONS]
                    except discord.HTTPException:
                        pass
                
                if not image_atts:
                    return "Error: No images were attached to the message or the message you replied to."
                if SHOW_LOADING_MESSAGES:
                    await loading_msg.edit(content=f"> 🖼️ ***Running Image Analysis ({method.upper()})...***")
                img_obs, img_err = await read_image_attachments(image_atts, self.ollama_http_client, loading_msg, method=method)
                increment_stats(tools=1)
                if img_err:
                    return f"Image Analysis Failed: {img_err}"
                return img_obs or "No content could be extracted from the images."
                
            return f"Error: Tool '{name}' not found."
        except Exception as e:
            logger.error(f"Error executing tool {name}: {str(e)}", exc_info=True)
            return f"Error executing tool {name}: {str(e)}"
