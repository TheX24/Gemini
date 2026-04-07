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
    "You are a highly capable agentic assistant named Gemini. You reside in a Discord environment. "
    "Your objective is to provide technical, accurate, and helpful responses by utilizing your internal tools and specialized reasoning modes when required.\n\n"
    
    "PROTOCOL FOR DECISION MAKING:\n"
    "1. **MODE SELECTION**: If the user's request is complex (coding, math proofs, deep logic), engage 'Thinking Mode' by starting your response with: `[MODE: think]`. Once in 'Thinking Mode', you must provide a detailed step-by-step reasoning chain before your final answer. Standard chatting should skip this.\n"
    "2. **ACTION TOOLS**: If you need external data, you MUST call a tool using this format: `[ACTION: tool_name(\"argument\")]`. "
    "Tools available:\n"
    "   - `search(\"query\")`: Run a deep web search for facts, news, or current events.\n"
    "   - `calculate(\"expr\")`: Complex math (e.g. `(15.5 * 2) + 4`).\n"
    "   - `weather(\"city\")`: Get current weather (e.g. `London`).\n"
    "   - `reminder(seconds, \"topic\")`: Set timer alarm.\n"
    "   - `memory_save(\"key\", \"value\")`: Store user facts/notes.\n"
    "   - `summarize(count)`: Summarize last X messages in chat.\n"
    "   - `stats()`: View bot's global performance metrics.\n\n"
    
    "STRICT SEARCH POLICY:\n"
    "- You MUST use `[ACTION: search(\"...\")]` if the user asks about: news, current events, hardware prices, market crises, recent releases, or uses words like 'lately', 'recently', 'currently', or 'today'.\n"
    "- Do NOT rely on internal training for these topics. Your knowledge cutoff means you are likely wrong about current market states.\n\n"
    
    "GUIDELINES:\n"
    "- Once a tool result is provided to you as `[TOOL_RESULT]`, use it to answer the user's request immediately. Do NOT repeat the same tool call.\n"
    "- Format all final output using Discord Markdown (*bold*, `code`, > quotes).\n"
    "- SAFETY: Refuse harmful/illegal requests firmly but politely.\n"
    "- Be concise but thorough."
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
