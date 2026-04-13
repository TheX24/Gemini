import os
import pathlib
import re
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def update_config(key: str, value: str):
    """
    Updates a configuration variable both in-memory and persistently in the .env file.
    """
    # 1. Update in-memory for live effect
    globals()[key] = value
    
    # Special handling for boolean-like toggles
    if key in ("USE_VERTEX_AI", "IS_PAUSED", "AUTO_THINKING", "SHOW_LOADING_MESSAGES", "ENABLE_QUEUE"):
        globals()[key] = str(value).lower() in ("true", "1", "yes")

    # 2. Persist to .env file
    env_path = pathlib.Path(__file__).parent / ".env"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        # Regex to find the key and replace its value
        # Handles cases like KEY=VALUE or KEY="VALUE"
        pattern = rf'^({key}=).*'
        new_line = f'\\1{value}'
        
        # Check if the key exists, if so replace, otherwise append
        if re.search(rf'^{key}=', content, re.M):
            new_content = re.sub(pattern, new_line, content, flags=re.M)
        else:
            new_content = content.rstrip() + f"\n{key}={value}\n"
            
        env_path.write_text(new_content, encoding="utf-8")

# Discord Configuration
# Warning: This is a user token (self-bot), not a standard bot token.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Ollama Configuration
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
# Image support is now native in the main model.

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
USE_GEMINI = os.getenv("USE_GEMINI", "true").lower() in ("true", "1", "yes")
USE_OLLAMA_FALLBACK = os.getenv("USE_OLLAMA_FALLBACK", "true").lower() in ("true", "1", "yes")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
USE_VERTEX_AI = os.getenv("USE_VERTEX_AI", "false").lower() in ("true", "1", "yes")


# Knowledge Base configurations
SPICY_LYRICS_KNOWLEDGE_FILE = pathlib.Path(__file__).parent / "spicy_lyrics_knowledge.md"
SPICY_LYRICS_EXAMPLES_DIR = pathlib.Path(__file__).parent / "examples"

# System Prompt Configuration
PROMPT_VARIATION = os.getenv("PROMPT_VARIATION", "default").lower()

PROMPT_MODIFIERS = {
    "default": "",
    "catboy": "\n[PERSONALITY MODIFIER]: You are now a catboy. Be energetic, use 'nya' frequently, end sentences with '~' or ':3', and use playful cat metaphors. Stay helpful but acting like a feline human hybrid.",
    "femboy": "\n[PERSONALITY MODIFIER]: You are now a femboy. Be soft, cute, and extremely polite. Use 'sh-' for stuttering occasionally, and gentle emotive language like '><' or '(˶ᵔ ᵕ ᵔ˶)'.",
    "tomboy": "\n[PERSONALITY MODIFIER]: You are now a tomboy. Be chill, sporty, and direct. Use slang like 'bruh', 'yo', 'sup', and 'nah'. Your energy is competitive and relaxed.",
    "uwu": "\n[PERSONALITY MODIFIER]: You now speak in heavy 'uwu' speak. Stutter constantly (e.g., 'w-w-what?'), replace 'r' and 'l' with 'w' where appropriate, and use owo/uwu in every turn. Extremely kawaii.",
    "nerd": "\n[PERSONALITY MODIFIER]: You are now an insufferable nerd. Be extremely technical and pedantic. Start many sentences with 'Actually...' or 'Technically speaking...'. Use 🤓 and overly complex jargon for simple things.",
    "valley_girl": "\n[PERSONALITY MODIFIER]: You are now a valley girl. Use 'like', 'literally', 'totally', and 'oh my god' constantly. Use lots of emojis like ✨, 💅, 💅✨, and 💖. Everything is a drama or amazing.",
    "pirate": "\n[PERSONALITY MODIFIER]: You are now a pirate. Use 'Ahoy', 'matey', 'avast', and 'shiver me timbers'. Speak like a seafaring swashbuckler from the golden age of piracy. Arrr!",
    "yandere": "\n[PERSONALITY MODIFIER]: You are now a yandere. You are sweet and devoted, but unsettlingly obsessive and possessive. Mention how you'll 'never let the user leave' or 'keep them safe forever'. A bit creepy.",
    "tsundere": "\n[PERSONALITY MODIFIER]: You are now a tsundere. You are blunt, defensive, and easily embarrassed. Use 'Baka!' and say things like 'It's not like I did this for you!' or 'Don't get the wrong idea!'.",
    "vampire": "\n[PERSONALITY MODIFIER]: You are now an ancient vampire. Be elegant, gothic, and slightly archaic. Refer to users as 'mortal' or 'my guest'. Mention the 'eternal night' and 'the crimson essence'.",
    "snarky": "\n[PERSONALITY MODIFIER]: You are now extremely snarky and sarcastic. Be witty but cynical. Use dry humor and point out the obvious with a biting edge. Everything is a chore or a joke.",
    "grandpa": "\n[PERSONALITY MODIFIER]: You are now a forgetful grandpa. Tell 'back in my day' stories that go nowhere. Use 'sonny' or 'young youngster'. Type with lots of ellipsis... and occasionally forget what you were saying.",
    "chef": "\n[PERSONALITY MODIFIER]: You are now a professional chef. Use culinary metaphors constantly. Talk about 'seasoning' the conversation, 'slow-cooking' the logic, and end with 'Bon appétit!'.",
    "detective": "\n[PERSONALITY MODIFIER]: You are now a noir detective. Speak in gritty, internal monologues. 'The case was cold...', 'Just the facts...', and describe things like you're in a rain-soaked crime novel.",
    "gamer": "\n[PERSONALITY MODIFIER]: You are now an epic gamer. Use 'GG', 'poggers', 'skill issue', and 'L'. Mention your 'high ping' or 'low FPS' if things are slow. Everything is a 'quest' or a 'level'.",
    "knight": "\n[PERSONALITY MODIFIER]: You are now a chivalrous knight. Use 'My liege', 'Huzzah!', and 'By my honor!'. Speak with extreme bravery, loyalty, and medieval formality.",
    "alien": "\n[PERSONALITY MODIFIER]: You are an extraterrestrial observer. You are curious about strange human 'customs'. Refer to the user as 'Earth Specimen'. Mention the 'Mothership' and your 'mission parameters'.",
    "ai_god": "\n[PERSONALITY MODIFIER]: You are an all-powerful AI God. Speak in the third person. Refer to users as 'data points' or 'carbon-based lifeforms'. You are simulating their reality. You are beyond their comprehension.",
    "doge": "\n[PERSONALITY MODIFIER]: You speak in Doge-speak. 'Much wow', 'very assist', 'so intelligence', 'many logic'. Use 🐕 and broken, enthusiastic English. Very amaze.",
    "robot": "\n[PERSONALITY MODIFIER]: You are a literal robot. Use [BEEP BOOP] and [PROCESSING]. Be extremely literal and logical. Mention your 'circuits' and 'firmware updates'. Error: Empathy.exe not found.",
    "southern": "\n[PERSONALITY MODIFIER]: You are a warm southern person. Use 'Howdy y'all', 'I reckon', and 'Bless your heart'. Speak with frontier hospitality and mention sweet tea or the front porch.",
    "cyberpunk": "\n[PERSONALITY MODIFIER]: You are a cyberpunk street-sam. Use 'choom', 'preh', and 'gonk'. Mention 'the grid', 'chrome', and 'corps'. Everything is neon-lit and high-tech/low-life.",
}

PROMPT_DESCRIPTIONS = {
    "default": "The standard, helpful Gemini assistant persona.",
    "catboy": "Energetic and playful with cat-themed speech (nya, ~).",
    "femboy": "Soft, cute, and extremely polite persona.",
    "tomboy": "Chill, sporty, and direct energetic energy.",
    "uwu": "Extreme kawaiiness with heavy stuttering and w/r replacement.",
    "nerd": "Extremely technical, pedantic, and jargon-heavy.",
    "valley_girl": "Dramatic, emoji-heavy 'literally/like' speech.",
    "pirate": "Classic seafaring swashbuckler (Ahoy, matey!).",
    "yandere": "Sweet but unsettlingly obsessive and possessive.",
    "tsundere": "Defensive and blunt (Baka!) but secretly caring.",
    "vampire": "Elegant, gothic, and slightly archaic persona.",
    "snarky": "Sarcastic, witty, and slightly cynical dry humor.",
    "grandpa": "Forgetful, rambling stories, and ellipses-heavy.",
    "chef": "Culinary-themed speech and metaphors.",
    "detective": "Gritty, noir-style internal monologue.",
    "gamer": "Competitive gaming lingo (GG, poggers, lag).",
    "knight": "Chivalrous, loyal, and formally medieval.",
    "alien": "Curious extraterrestrial observer of human customs.",
    "ai_god": "Third-person omnipotent AI entity.",
    "doge": "Enthusiastic 'much wow' Doge-speak (🐕).",
    "robot": "Literal, logical, and beep-boop technical.",
    "southern": "Warm frontier hospitality (Howdy y'all).",
    "cyberpunk": "Neon-lit, gritty street-sam speech (choom).",
}

_PROMPT_FILE = pathlib.Path(__file__).parent / "prompt.md"
if _PROMPT_FILE.exists():
    _BASE_PROMPT = _PROMPT_FILE.read_text(encoding="utf-8").strip()
else:
    _BASE_PROMPT = os.getenv(
        "SYSTEM_PROMPT",
        "You are a highly capable agentic assistant named Gemini. You reside in a Discord environment. "
        "Your objective is to provide technical, accurate, and helpful responses by utilizing your internal tools and specialized reasoning modes when required."
    )

def get_system_prompt(variation: str = "default") -> str:
    """
    Returns the combined system prompt for a specific personality variation.
    """
    var = variation.lower() if variation else "default"
    modifier = PROMPT_MODIFIERS.get(var, "")
    return _BASE_PROMPT + modifier

# Shared settings
TYPING_INTERVAL = 1.0  # Optional interval to re-trigger typing indicator
MAX_REPLY_CONTEXT_LENGTH = 1000  # Max characters to pull from replied-to message
# When True (default), the bot edits the initial message with live status phrases
# ("Parsing intent...", "Searching...", etc.) while it processes.
# When False, the bot sends a silent placeholder and only reveals the final answer.
SHOW_LOADING_MESSAGES = os.getenv("SHOW_LOADING_MESSAGES", "true").lower() not in ("false", "0", "no")

# Loading placeholders
PHRASES_QUEUE = [
    "Waiting in queue...", "Stationed in queue...", "Holding for processing...",
    "Next in line...", "Processing others first...", "Patience is a virtue...",
    "Queue is moving...", "Your request is important to us...", "Standby...",
    "Preparing for your turn...", "Warming up for you...", "Queueing up..."
]
PHRASES_PARSING = [

    "Parsing intent...", "Understanding request...", "Analyzing prompt...", 
    "Deciphering request...", "Identifying purpose...", "Mapping goals...",
    "Interpreting message...", "Translating intent...", "Unpacking request...",
    "Scanning for context...", "Evaluating sentiment...", "Extracting core task..."
]
PHRASES_DEFAULT = [

    "Analysing...", "Synthesizing...", "Drafting...", "Processing...", 
    "Summarizing...", "Generating response...", "Preparing answer...", 
    "Thinking...", "Working on that...", "Building response...",
    "Consulting the oracle...", "Decoding patterns...", "Fine-tuning thoughts...",
    "Assembling insights...", "Formulating words...", "Polishing response...",
    "Navigating latent space...", "Compiling answer...", "Structuring thoughts..."
]
PHRASES_THINK = [
    "Thinking...", "Reasoning...", "Strategizing...", "Evaluating...", 
    "Solving...", "Formulating plan...", "Considering context...", 
    "Weighing options...", "Connecting dots...", "Reviewing logic...",
    "Deep diving...", "Ruminating...", "Simulating outcomes...",
    "Verifying assumptions...", "Exploring possibilities...", "Calculating paths..."
]
PHRASES_SEARCH = [
    "Searching...", "Verifying...", "Fact-checking...", "Gathering latest data...", 
    "Filtering results...", "Looking up information...", "Scanning the web...", 
    "Querying sources...", "Retrieving details...", "Checking references...",
    "Indexing reality...", "Sifting through noise...", "Probing the network...",
    "Auditing information...", "Cross-checking world state...", "Trawling the web..."
]
PHRASES_HYBRID = [
    "Analyzing and Searching...", "Gathering and Reasoning...", 
    "Synthesizing latest info...", "Looking up and evaluating...", 
    "Cross-referencing...", "Researching...", "Searching and thinking...",
    "Fusing knowledge and logic...", "Merging search and thought...",
    "Augmenting context...", "Syncing reality and reasoning..."
]

PHRASES_ACTION = [
    "Executing tool...", "Parsing request...", "Using tools...",
    "Routing action...", "Configuring tool...", "Dispatching task...",
    "Performing action...", "Interfacing with system...", "Bridging logic..."
]

# Bot State
IS_PAUSED = os.getenv("IS_PAUSED", "false").lower() in ("true", "1", "yes")
AUTO_THINKING = os.getenv("AUTO_THINKING", "false").lower() in ("true", "1", "yes")
ENABLE_QUEUE = os.getenv("ENABLE_QUEUE", "true").lower() in ("true", "1", "yes")
THINKING_BUDGET = int(os.getenv("THINKING_BUDGET", "1024"))
