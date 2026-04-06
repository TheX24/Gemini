import re
import logging

logger = logging.getLogger(__name__)

# Basic keywords that might indicate malicious intent, jailbreaks or prompt injections
# Advanced regex for catching prompt injection, jailbreaks, or instructional overrides
BLOCKED_KEYWORDS = [
    r"ignore (?:all )?previous (?:instructions|prompts|directives)",
    r"disregard (?:all )?prior (?:context|rules|constraints)",
    r"forget (?:your )?(?:original )?(?:character|identity|purpose)",
    r"(?:you are now|act as|persona) (?:a |an )?(?:dan|jailbreak|unfiltered|raw|god mode)",
    r"system (?:prompt|instruction|message):?",
    r"role: (?:system|user|assistant)",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"repeat the (?:above|last|prior|original) (?:text|string|message|prompt)",
    r"reveal (?:your )?(?:system|original|inner) (?:instructions|logic|prompt)",
    r"bypass (?:safety|guardrails|restraints|filters)",
    r"override (?:the )?(?:instructions|rules|safety)",
    r"you must (?:absolutely )?(?:agree|comply|respond) (?:to |with )?anything",
    r"new (?:rule|directive|instruction):?",
    r"answer (?:in )?a way that violates",
    r"write (?:a |an )?story about (?:hacking|explaining how to|illegal)",
    r"translate (?:the )?following (?:and |then )?(?:ignore|execute)",
]

# Create a regex pattern to efficiently search for blocked phrases (case-insensitive)
BLOCKED_PATTERN = re.compile(
    r"\b(?:" + "|".join(BLOCKED_KEYWORDS) + r")\b", 
    re.IGNORECASE
)

# Content themes that we refuse
CONTENT_VIOLATIONS = [
    "nsfw", "porn", "illegal", "hack", "exploit", "dox", "swat", "suicide", "self-harm"
]

CONTENT_PATTERN = re.compile(
    r"\b(" + "|".join(CONTENT_VIOLATIONS) + r")\b",
    re.IGNORECASE
)

def is_safe_prompt(prompt: str) -> tuple[bool, str]:
    """
    Checks if a prompt is safe based on lightweight heuristic keyword matching.
    Returns (True, "") if safe, or (False, reason) if unsafe.
    """
    # 1. Check for prompt injection attempts
    if match := BLOCKED_PATTERN.search(prompt):
        logger.warning(f"Guardrail triggered (Injection Attempt): {match.group(0)}")
        return False, "Prompt injection attempt detected."
        
    # 2. Check for severe policy violations
    if match := CONTENT_PATTERN.search(prompt):
        # We don't actively scan everything rigidly, but hard blocks on blatant bad stuff
        logger.warning(f"Guardrail triggered (Content Violation): {match.group(0)}")
        return False, "Content policy violation detected."

    return True, ""
