import os
import pathlib
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Discord Configuration
# Warning: This is a user token (self-bot), not a standard bot token.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Ollama Configuration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
# Optional: set to a vision-capable model to enable image understanding.
# Examples: "moondream", "llava", "qwen2-vl", "minicpm-v"
# Leave empty to fall back to pytesseract OCR (requires: pip install pytesseract pillow + sudo apt install tesseract-ocr)
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "")
# Number of GPU layers for the vision model.
# 0 = fully on CPU/RAM (keeps VRAM free for main model) — recommended for co-existence
# -1 = fully on GPU (fastest, but evicts main model)
VISION_NUM_GPU = int(os.getenv("VISION_NUM_GPU", "0"))

# SearXNG Configuration
SEARXNG_BASE_URL = os.getenv("SEARXNG_BASE_URL", "http://localhost:8888")
SEARXNG_FORMAT = os.getenv("SEARXNG_FORMAT", "json")
SEARXNG_CATEGORIES = os.getenv("SEARXNG_CATEGORIES", "general")

# System Prompt for the assistant
# Loads from prompt.md (next to this file) first, then falls back to SYSTEM_PROMPT env var.
_PROMPT_FILE = pathlib.Path(__file__).parent / "prompt.md"
if _PROMPT_FILE.exists():
    DEFAULT_SYSTEM_PROMPT = _PROMPT_FILE.read_text(encoding="utf-8").strip()
else:
    DEFAULT_SYSTEM_PROMPT = os.getenv(
        "SYSTEM_PROMPT",
        "You are a highly capable agentic assistant named Gemini. You reside in a Discord environment. "
        "Your objective is to provide technical, accurate, and helpful responses by utilizing your internal tools and specialized reasoning modes when required."
    )

# Shared settings
TYPING_INTERVAL = 1.0  # Optional interval to re-trigger typing indicator
MAX_REPLY_CONTEXT_LENGTH = 1000  # Max characters to pull from replied-to message
# When True (default), the bot edits the initial message with live status phrases
# ("Parsing intent...", "Searching...", etc.) while it processes.
# When False, the bot sends a silent placeholder and only reveals the final answer.
SHOW_LOADING_MESSAGES = os.getenv("SHOW_LOADING_MESSAGES", "true").lower() not in ("false", "0", "no")

# Loading placeholders
PHRASES_DEFAULT = [
    "Analysing...", "Synthesizing...", "Drafting...", "Processing...", 
    "Summarizing...", "Generating response...", "Preparing answer...", 
    "Thinking...", "Working on that...", "Building response..."
]
PHRASES_THINK = [
    "Thinking...", "Reasoning...", "Strategizing...", "Evaluating...", 
    "Solving...", "Formulating plan...", "Considering context...", 
    "Weighing options...", "Connecting dots...", "Reviewing logic..."
]
PHRASES_SEARCH = [
    "Searching...", "Verifying...", "Fact-checking...", "Gathering latest data...", 
    "Filtering results...", "Looking up information...", "Scanning the web...", 
    "Querying sources...", "Retrieving details...", "Checking references..."
]
PHRASES_HYBRID = [
    "Analyzing and Searching...", "Gathering and Reasoning...", 
    "Synthesizing latest info...", "Looking up and evaluating...", 
    "Cross-referencing...", "Researching...", "Searching and thinking..."
]

PHRASES_ACTION = [
    "Executing tool...", "Parsing request...", "Using tools...",
    "Routing action...", "Configuring tool..."
]
