import sqlite3
import logging
from typing import List, Dict, Any
import json
import time

logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            # Reminders table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    trigger_time INTEGER NOT NULL,
                    topic TEXT NOT NULL
                )
            """)
            
            # Memory table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    UNIQUE(user_id, key)
                )
            """)
            
            # Stats table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    messages_answered INTEGER DEFAULT 0,
                    tokens_used INTEGER DEFAULT 0,
                    searches_run INTEGER DEFAULT 0,
                    tools_used INTEGER DEFAULT 0
                )
            """)
            
            # User Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    variation TEXT DEFAULT 'default'
                )
            """)
            
            # Message Variations table for reply chains
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS message_variations (
                    message_id INTEGER PRIMARY KEY,
                    variation TEXT NOT NULL
                )
            """)
            # Keyword Memories table (for global facts triggered by words)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS keyword_memories (
                    keyword TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # System State table (for persistence across restarts)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # Whitelist table for privileged commands
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS whitelist (
                    user_id INTEGER PRIMARY KEY
                )
            """)
            
            # Channel Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_settings (
                    channel_id INTEGER PRIMARY KEY,
                    variation TEXT DEFAULT 'default'
                )
            """)
            
            # Server Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS server_settings (
                    server_id INTEGER PRIMARY KEY,
                    variation TEXT DEFAULT 'default'
                )
            """)
            
            # Initialize exactly one row
            cursor.execute("INSERT OR IGNORE INTO stats (id) VALUES (1)")
            
            conn.commit()
            logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing DB: {e}")

def add_reminder(channel_id: int, message_id: int, trigger_time: int, topic: str) -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO reminders (channel_id, message_id, trigger_time, topic) VALUES (?, ?, ?, ?)",
                (channel_id, message_id, trigger_time, topic)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding reminder: {e}")
        return False

def get_due_reminders() -> List[Dict[str, Any]]:
    # Get reminders where trigger_time <= now
    current_time = int(time.time())
    reminders = []
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM reminders WHERE trigger_time <= ?", (current_time,))
            rows = cursor.fetchall()
            for row in rows:
                reminders.append(dict(row))
    except Exception as e:
        logger.error(f"Error fetching reminders: {e}")
    return reminders

def delete_reminder(reminder_id: int):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"Error deleting reminder {reminder_id}: {e}")

def save_memory(user_id: int, key: str, value: str) -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO memory (user_id, key, value) VALUES (?, ?, ?)",
                (user_id, key, value)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error saving memory: {e}")
        return False

def get_memories(user_id: int) -> List[Dict[str, Any]]:
    memories = []
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM memory WHERE user_id = ?", (user_id,))
            for row in cursor.fetchall():
                memories.append(dict(row))
    except Exception as e:
        logger.error(f"Error fetching memory: {e}")
    return memories

def increment_stats(messages: int = 0, tokens: int = 0, searches: int = 0, tools: int = 0):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE stats SET 
                    messages_answered = messages_answered + ?,
                    tokens_used = tokens_used + ?,
                    searches_run = searches_run + ?,
                    tools_used = tools_used + ?
                WHERE id = 1
            """, (messages, tokens, searches, tools))
            conn.commit()
    except Exception as e:
        logger.error(f"Error incrementing stats: {e}")

def get_stats() -> Dict[str, int]:
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stats WHERE id = 1")
            row = cursor.fetchone()
            if row:
                res = dict(row)
                # Ensure we don't return None for any of these
                return {
                    "messages_answered": res.get("messages_answered") or 0,
                    "tokens_used": res.get("tokens_used") or 0,
                    "searches_run": res.get("searches_run") or 0,
                    "tools_used": res.get("tools_used") or 0
                }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
    return {"messages_answered": 0, "tokens_used": 0, "searches_run": 0, "tools_used": 0}

# ── User Settings Management ──────────────────────────────────────────

def get_user_settings(user_id: int) -> Dict[str, Any]:
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
    except Exception as e:
        logger.error(f"Error fetching user settings for {user_id}: {e}")
    
    # Default return if not found
    return {"variation": "default"}

def save_user_variation(user_id: int, variation: str):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_settings (user_id, variation) 
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    variation = excluded.variation
            """, (user_id, variation))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving user variation: {e}")

def get_channel_settings(channel_id: int) -> Dict[str, Any]:
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channel_settings WHERE channel_id = ?", (channel_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
    except Exception as e:
        logger.error(f"Error fetching channel settings for {channel_id}: {e}")
    return {"variation": "default"}

def save_channel_variation(channel_id: int, variation: str):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_settings (channel_id, variation) 
                VALUES (?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET 
                    variation = excluded.variation
            """, (channel_id, variation))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving channel variation: {e}")

def get_server_settings(server_id: int) -> Dict[str, Any]:
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM server_settings WHERE server_id = ?", (server_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
    except Exception as e:
        logger.error(f"Error fetching server settings for {server_id}: {e}")
    return {"variation": "default"}

def save_server_variation(server_id: int, variation: str):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO server_settings (server_id, variation) 
                VALUES (?, ?)
                ON CONFLICT(server_id) DO UPDATE SET 
                    variation = excluded.variation
            """, (server_id, variation))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving server variation: {e}")

def get_message_variation(message_id: int) -> str | None:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT variation FROM message_variations WHERE message_id = ?", (message_id,))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"Error fetching message variation for {message_id}: {e}")
    return None

def save_message_variation(message_id: int, variation: str):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO message_variations (message_id, variation) VALUES (?, ?)",
                (message_id, variation)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving message variation: {e}")

# ── System State Management ──────────────────────────────────────────────

def save_system_state(key: str, value: str | None):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            if value is None:
                cursor.execute("DELETE FROM system_state WHERE key = ?", (key,))
            else:
                cursor.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving system state {key}: {e}")

def get_system_state(key: str) -> str | None:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"Error fetching system state {key}: {e}")
    return None

# ── Keyword Memory Management ──────────────────────────────────────────

def save_keyword_memory(keyword: str, value: str | None):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            if value is None:
                cursor.execute("DELETE FROM keyword_memories WHERE keyword = ?", (keyword.lower(),))
            else:
                cursor.execute("INSERT OR REPLACE INTO keyword_memories (keyword, value) VALUES (?, ?)", (keyword.lower(), value))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving keyword memory {keyword}: {e}")

def get_keyword_memories() -> List[Dict[str, str]]:
    """Returns all keyword-value pairs for scanning."""
    mems = []
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM keyword_memories")
            for row in cursor.fetchall():
                mems.append(dict(row))
    except Exception as e:
        logger.error(f"Error fetching keyword memories: {e}")
    return mems

# ── Whitelist Management ──────────────────────────────────────────────

def add_to_whitelist(user_id: int) -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding to whitelist: {e}")
        return False

def remove_from_whitelist(user_id: int) -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error removing from whitelist: {e}")
        return False

def is_whitelisted(user_id: int) -> bool:
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking whitelist: {e}")
        return False

def toggle_whitelist(user_id: int) -> bool:
    """Toggles whitelisted status. Returns new status."""
    try:
        current = is_whitelisted(user_id)
        if current:
            remove_from_whitelist(user_id)
            return False
        else:
            add_to_whitelist(user_id)
            return True
    except Exception as e:
        logger.error(f"Error toggling whitelist: {e}")
        return False

# ── Budget Management ────────────────────────────────────────────────

def get_budget_spent() -> float:
    """Returns the total spent today. Resets if date changed."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    last_reset = get_system_state("budget_last_reset")
    
    if last_reset != today:
        save_system_state("budget_spent_today", "0.0")
        save_system_state("budget_last_reset", today)
        return 0.0
    
    spent_str = get_system_state("budget_spent_today")
    return float(spent_str) if spent_str else 0.0

def add_to_budget_spent(amount: float):
    current = get_budget_spent()
    new_total = current + amount
    save_system_state("budget_spent_today", str(new_total))

