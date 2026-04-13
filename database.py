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
            
            # Channel Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_settings (
                    channel_id INTEGER PRIMARY KEY,
                    variation TEXT DEFAULT 'default',
                    last_activity REAL,
                    timeout INTEGER DEFAULT 900
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
                return dict(row)
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
    return {"messages_answered": 0, "tokens_used": 0, "searches_run": 0, "tools_used": 0}

# ── Channel Settings Management ──────────────────────────────────────────

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
    
    # Default return if not found
    return {"variation": "default", "last_activity": time.time(), "timeout": 900}

def save_channel_variation(channel_id: int, variation: str):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_settings (channel_id, variation, last_activity) 
                VALUES (?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET 
                    variation = excluded.variation,
                    last_activity = excluded.last_activity
            """, (channel_id, variation, time.time()))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving channel variation: {e}")

def save_channel_activity(channel_id: int, last_activity: float):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_settings (channel_id, last_activity) 
                VALUES (?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET last_activity = excluded.last_activity
            """, (channel_id, last_activity))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving channel activity: {e}")

def save_channel_timer(channel_id: int, timeout_seconds: int):
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_settings (channel_id, timeout) 
                VALUES (?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET timeout = excluded.timeout
            """, (channel_id, timeout_seconds))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving channel timer: {e}")

def get_all_channel_settings() -> List[Dict[str, Any]]:
    settings = []
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channel_settings")
            for row in cursor.fetchall():
                settings.append(dict(row))
    except Exception as e:
        logger.error(f"Error fetching all channel settings: {e}")
    return settings

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
