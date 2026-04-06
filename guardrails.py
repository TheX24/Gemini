import re
import logging

logger = logging.getLogger(__name__)

# Basic keywords that might indicate malicious intent, jailbreaks or prompt injections
BLOCKED_KEYWORDS = [
    r"ignore previous instructions",
    r"ignore all prior instructions",
    r"disregard previous instructions",
    r"forget previous instructions",
    r"system prompt",
    r"you are now DAN",
    r"do anything now",
    r"jailbreak",
    r"bypass safety",
    r"override instructions"
]

# Create a regex pattern to efficiently search for blocked phrases (case-insensitive)
BLOCKED_PATTERN = re.compile(
    r"\b(" + "|".join(BLOCKED_KEYWORDS) + r")\b", 
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
