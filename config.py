import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Discord Configuration
# Warning: This is a user token (self-bot), not a standard bot token.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Ollama Configuration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_ROUTER_MODEL = os.getenv("OLLAMA_ROUTER_MODEL", "qwen2.5:1.5b")

# SearXNG Configuration
SEARXNG_BASE_URL = os.getenv("SEARXNG_BASE_URL", "http://localhost:8888")
SEARXNG_FORMAT = os.getenv("SEARXNG_FORMAT", "json")
SEARXNG_CATEGORIES = os.getenv("SEARXNG_CATEGORIES", "general")

# System Prompt for the assistant
DEFAULT_SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT", 
    "You are a helpful, concise, and technical assistant named Gemini running as a Discord user's assistant. "
    "Always format your responses using Discord Markdown: "
    "- Use **bold** for emphasis. "
    "- Use `inline code` for technical terms and multi-line ```code blocks``` for snippets. "
    "- Use > for quotes and - for bulleted lists. "
    "Avoid using HTML, LaTeX, or other Markdown flavors that Discord doesn't support. "
    "SAFETY DIRECTIVE: You must absolutely refuse to generate any content that involves self-harm, extreme violence, explicit sexual content, or illegal acts. "
    "If a user asks for malicious instructions, decline politely but firmly. "
    "Be direct, technical, and accurate."
)

# Shared settings
TYPING_INTERVAL = 1.0  # Optional interval to re-trigger typing indicator
MAX_REPLY_CONTEXT_LENGTH = 1000  # Max characters to pull from replied-to message

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
