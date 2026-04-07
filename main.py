import asyncio
import logging
import httpx
import signal
from config import DISCORD_TOKEN, SEARXNG_BASE_URL
from bot import GeminiSelfBot
from searxng_manager import SearxngManager

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
logging.getLogger("searxng_manager").setLevel(logging.WARNING)

logger = logging.getLogger("main")

async def main():
    # 0. Start SearXNG Manager
    searxng = SearxngManager(SEARXNG_BASE_URL)
    await searxng.start()

    # 1. Initialize the shared AsyncClient for Ollama
    async with httpx.AsyncClient() as ollama_client:
        
        # 2. Instantiate the self-bot
        bot = GeminiSelfBot(ollama_http_client=ollama_client)
        
        try:
            # 3. Run the bot
            # Note: bot.run() is blocking, so we use start() inside an async context 
            # or just use run() if it's the top-level. 
            # In discord.py-self, bot.start() is typically used for async setup.
            await bot.start(DISCORD_TOKEN)
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            if not bot.is_closed():
                await bot.close()
            searxng.stop()
            logger.info("Bot shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Exit requested by user.")
