import asyncio
import logging
import httpx
import signal
import os
import sys
from config import DISCORD_TOKEN
from bot import GeminiSelfBot

# Configure logging
log_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
date_format = '%Y-%m-%d %H:%M:%S'

# Root logger configuration
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=date_format,
    handlers=[
        logging.FileHandler("gemini_bot.log"),      # Save everything to file
        logging.StreamHandler()                    # Terminal output
    ]
)

# Silence noisy libraries on the terminal (but they remain in the log file)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("discord").setLevel(logging.WARNING)

logger = logging.getLogger("main")

LOCK_FILE = "bot.pid"

_OWN_LOCK = False

def check_single_instance():
    """Ensures only one instance of the bot is running."""
    global _OWN_LOCK
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                pid = int(f.read().strip())
            
            if pid == os.getpid():
                _OWN_LOCK = True
                return

            # signal 0 is used to check if process exists without killing it
            os.kill(pid, 0)
            logger.error(f"FATAL: Another instance is already running (PID: {pid}). Exiting to prevent duplication.")
            sys.exit(1)
        except (ValueError, OSError, ProcessLookupError):
            logger.warning("Stale lock file found. Overwriting...")
            pass
    
    try:
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
        _OWN_LOCK = True
    except Exception as e:
        logger.error(f"Failed to create lock file: {e}")

def remove_lock():
    """Removes the instance lock file on exit if we own it."""
    global _OWN_LOCK
    if _OWN_LOCK and os.path.exists(LOCK_FILE):
        try:
            os.remove(LOCK_FILE)
            _OWN_LOCK = False
        except:
            pass

async def main():
    # 0. Ensure single instance
    check_single_instance()
    
    # 1. Initialize the shared AsyncClient for Ollama
    async with httpx.AsyncClient() as ollama_client:
        
        # 2. Instantiate the self-bot
        bot = GeminiSelfBot(ollama_http_client=ollama_client)
        
        try:
            # 3. Run the bot
            await bot.start(DISCORD_TOKEN, reconnect=True)
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            if not bot.is_closed():
                await bot.close()
            remove_lock()
            logger.info("Bot shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        remove_lock()
        logger.info("Exit requested by user.")
    except SystemExit as e:
        remove_lock()
        raise e
    except Exception as e:
        remove_lock()
        logger.error(f"Unexpected crash: {e}")
        sys.exit(1)
