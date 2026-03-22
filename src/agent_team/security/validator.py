"""Input validation and sanitization."""
import re
from agent_team.config import MAX_INPUT_LENGTH


class ValidationError(Exception):
    pass


def sanitize_text(text: str) -> str:
    """Strip control characters but preserve newlines and tabs."""
    # Remove null bytes and other control chars except \n, \r, \t
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return cleaned


def validate_plan_input(text: str) -> str:
    """Validate and sanitize user plan input."""
    if not text or not text.strip():
        raise ValidationError("Plan input cannot be empty")
    if len(text) > MAX_INPUT_LENGTH:
        raise ValidationError(f"Plan input exceeds maximum length of {MAX_INPUT_LENGTH} characters")
    return sanitize_text(text.strip())


def validate_execution_path(path: str | None) -> str | None:
    """Validate an execution path."""
    if not path:
        return None
    path = path.strip()
    if not path:
        return None
    # Block obviously dangerous paths
    dangerous = ["/etc", "/usr", "/bin", "/sbin", "/var", "/System", "/Library"]
    from pathlib import Path as P
    resolved = str(P(path).expanduser().resolve())
    for d in dangerous:
        if resolved == d or resolved.startswith(d + "/"):
            raise ValidationError(f"Execution path '{path}' is in a restricted system directory")
    return path


def validate_mode(mode: str) -> str:
    """Validate agent mode string."""
    valid_modes = {"thinking", "coding", "brainstorming", "architecture", "execution"}
    mode = mode.strip().lower()
    if mode not in valid_modes:
        raise ValidationError(f"Invalid mode '{mode}'. Must be one of: {', '.join(sorted(valid_modes))}")
    return mode
